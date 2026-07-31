"""Ghostscript-обгортка: RGB-вектор -> CMYK/PDF-X з ICC (guardrail §4, DECISIONS).

Рішення (docs/DECISIONS.md): CMYK та PDF-X робимо через Ghostscript з ICC
(FOGRA39/SWOP), а не через «native CMYK» бібліотек рендеру. Шлях до ICC — у
змінній оточення PRINT_ICC_PROFILE. Якщо профіль не заданий, gs усе одно
конвертує у DeviceCMYK своїм дефолтним профілем, а функція логує попередження
(файл виходить CMYK, але не повноцінний PDF/X).

Помилки gs піднімаються як RuntimeError з контекстом; шар API транслює у JSON.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile

logger = logging.getLogger("vektralogos.cmyk")


def _gs_binary() -> str:
    gs = shutil.which("gs") or shutil.which("gswin64c") or shutil.which("gswin32c")
    if gs is None:
        raise RuntimeError(
            "Ghostscript не знайдено у PATH. Встанови: brew install ghostscript"
        )
    return gs


# Шаблон PDFX_def.ps: реєструє ICC як OutputIntent для PDF/X-3.
_PDFX_DEF_TEMPLATE = """%%!
[ /_objdef {{icc_PDFX}} /type /stream /OBJ pdfmark
[ {{icc_PDFX}} << /N 4 >> /PUT pdfmark
[ {{icc_PDFX}} ({icc_path}) (r) file /PUT pdfmark
[ /_objdef {{OutputIntent_PDFX}} /type /dict /OBJ pdfmark
[ {{OutputIntent_PDFX}} <<
    /Type /OutputIntent
    /S /GTS_PDFX
    /OutputConditionIdentifier ({condition})
    /Info ({condition})
    /DestOutputProfile {{icc_PDFX}}
  >> /PUT pdfmark
[ {{Catalog}} << /OutputIntents [ {{OutputIntent_PDFX}} ] >> /PUT pdfmark
"""


def _resolve_icc(icc_profile: str | None) -> str | None:
    icc = icc_profile if icc_profile is not None else os.environ.get("PRINT_ICC_PROFILE")
    if not icc:
        return None
    if not os.path.exists(icc):
        logger.warning("PRINT_ICC_PROFILE вказує на неіснуючий файл: %s", icc)
        return None
    return icc


def to_cmyk_pdf(
    rgb_pdf: bytes,
    icc_profile: str | None = None,
    *,
    condition: str = "FOGRA39",
) -> bytes:
    """Конвертує вхідний PDF у DeviceCMYK через Ghostscript.

    Якщо доступний ICC-профіль (аргумент або PRINT_ICC_PROFILE) — виводить
    PDF/X-3 з output intent на цьому профілі. Інакше — простий CMYK PDF
    (з попередженням у лог).
    """
    gs = _gs_binary()
    icc = _resolve_icc(icc_profile)

    with tempfile.TemporaryDirectory(prefix="vektralogos_") as tmp:
        in_pdf = os.path.join(tmp, "in.pdf")
        out_pdf = os.path.join(tmp, "out.pdf")
        with open(in_pdf, "wb") as fh:
            fh.write(rgb_pdf)

        cmd = [
            gs,
            "-dSAFER",
            "-dBATCH",
            "-dNOPAUSE",
            "-dNOOUTERSAVE",
            "-sDEVICE=pdfwrite",
            "-dPDFSETTINGS=/prepress",
            "-sProcessColorModel=DeviceCMYK",
            "-sColorConversionStrategy=CMYK",
            f"-sOutputFile={out_pdf}",
        ]

        if icc is not None:
            def_ps = os.path.join(tmp, "PDFX_def.ps")
            with open(def_ps, "w", encoding="ascii") as fh:
                fh.write(
                    _PDFX_DEF_TEMPLATE.format(
                        icc_path=icc.replace("\\", "/"),
                        condition=condition,
                    )
                )
            cmd += [
                "-dPDFX",
                "-dOverrideICC=true",
                f"-sOutputICCProfile={icc}",
                def_ps,
            ]
        else:
            logger.warning(
                "ICC-профіль не заданий (PRINT_ICC_PROFILE) — вивід буде CMYK, "
                "але НЕ повноцінний PDF/X. Preflight має це позначити."
            )

        cmd.append(in_pdf)

        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0 or not os.path.exists(out_pdf):
            raise RuntimeError(
                "Ghostscript завершився з помилкою "
                f"(код {proc.returncode}).\nSTDERR:\n{proc.stderr}\nSTDOUT:\n{proc.stdout}"
            )
        with open(out_pdf, "rb") as fh:
            return fh.read()

.PHONY: venv install render prompt serve test clean fetch-icc

ICC_URL  := https://eci.org/lib/exe/eci_offset_2009.zip
ICC_FILE := server/icc/ISOcoated_v2_eci.icc

VENV ?= .venv
PY   := $(VENV)/bin/python
PIP  := $(VENV)/bin/pip
SPEC ?= examples/hello.json
OUT  ?= print.pdf

venv:
	python3 -m venv $(VENV)

install: venv
	$(PIP) install --upgrade pip
	$(PIP) install -r requirements.txt

# JSON -> print.pdf.  Приклад: make render SPEC=examples/hello.json OUT=card.pdf
render:
	$(PY) -m server.cli render $(SPEC) -o $(OUT)

# prompt -> Canvas JSON -> print.pdf. Приклад: make prompt PROMPT="візитка кав'ярні"
prompt:
	$(PY) -m server.cli prompt "$(PROMPT)" -o $(OUT) --spec-out $(OUT).json

# Завантажити FOGRA39 ICC (ISO Coated v2, ECI) — локально, не в репо.
fetch-icc:
	@mkdir -p server/icc
	@echo "Downloading ECI offset 2009 (ISO Coated v2 / FOGRA39)…"
	curl -sL -o /tmp/eci_offset_2009.zip $(ICC_URL)
	unzip -o -j /tmp/eci_offset_2009.zip "ECI_Offset_2009/ISOcoated_v2_eci.icc" -d server/icc
	rm -f /tmp/eci_offset_2009.zip
	@echo "Done: $(ICC_FILE)"
	@echo "Тепер додай у .env:  PRINT_ICC_PROFILE=$(CURDIR)/$(ICC_FILE)"

# Editor MVP: локальний редактор у браузері (prompt -> превью -> print-ready PDF).
# Відкрий http://127.0.0.1:8000. Потрібен ANTHROPIC_API_KEY у .env для /api/brief.
PORT ?= 8000
serve:
	$(PY) -m uvicorn server.api:app --host 127.0.0.1 --port $(PORT) --reload

test:
	$(PY) -m pytest -q

clean:
	rm -f *.pdf *.pdf.json
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

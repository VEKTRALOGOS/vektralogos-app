.PHONY: venv install render prompt test clean

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

test:
	$(PY) -m pytest -q

clean:
	rm -f *.pdf *.pdf.json
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

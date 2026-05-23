.PHONY: setup run validate test clean

# Setup virtual environment and install dependencies
setup:
	python3 -m venv .venv
	.venv/bin/pip install --upgrade pip
	.venv/bin/pip install -r requirements.txt
	cp -n .env.example .env 2>/dev/null || true
	@echo "Setup complete. Edit .env with your API keys."

# Run the agent (paper mode by default)
run:
	.venv/bin/python main.py --mode paper

# Run signal validation (Phase 1 key test)
validate:
	.venv/bin/python scripts/validate_signal.py --num-markets 50 --model sonnet

validate-small:
	.venv/bin/python scripts/validate_signal.py --num-markets 10 --model haiku

# Initialize the database
db-init:
	.venv/bin/python -c "from core.db import Database; Database('agent.db'); print('DB initialized')"

# Clean build artifacts
clean:
	rm -rf __pycache__ */__pycache__ */*/__pycache__
	rm -f agent.db
	rm -f validation_results.json

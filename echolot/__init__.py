"""echolot — a deterministic layer between Perfetto traces and AI agents."""

# The single source. `pyproject.toml` reads it from here, so a checkout and a
# wheel cannot disagree — and neither can an editable install whose metadata
# was written three versions ago. `doctor` printed 0.1.0 against 0.4.0 in
# pyproject for exactly that reason, and that number goes into every line of
# the run log and into the .claude/ layer manifest.
__version__ = "0.5.1"

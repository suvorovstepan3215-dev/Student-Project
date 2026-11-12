"""Offline smoke check for this standalone Streamlit kit.

Verifies that the app imports THIS folder (not another checkout), renders its
public UI, uses the two coursework sources and really computes LSA. No source
API request is made and no run button is pressed.
"""

from pathlib import Path

import utils
from summarizer import summarize_with_details
from streamlit.testing.v1 import AppTest


def main() -> int:
    directory = Path(__file__).resolve().parent
    if utils.BASE_DIR != directory:
        raise RuntimeError(f"Imported another copy: {utils.BASE_DIR}")
    if not (directory / "diagr" / "agents.html").is_file():
        raise RuntimeError("diagr/agents.html is missing.")
    app = AppTest.from_file(str(directory / "streamlit_app.py"), default_timeout=20).run()
    if app.exception:
        raise RuntimeError("Streamlit rendering failed; inspect the traceback above.")
    if app.multiselect[0].value != ["europepmc", "minzdrav"]:
        raise RuntimeError("Unexpected default sources.")
    if app.text_input:
        raise RuntimeError("Public UI must not ask for endpoints, keys or env names.")
    if [option for option in app.radio[0].options] != ["Локальный LSA (без LLM)"]:
        raise RuntimeError("Public UI must default to local summarization only.")
    english = summarize_with_details("Agents collect papers. Another agent checks relevance.", 1)
    russian = summarize_with_details("Агенты собирают публикации. Другой агент проверяет релевантность.", 1)
    if not english.text or not russian.text:
        raise RuntimeError("LSA returned an empty summary.")
    if not english.method.startswith(("sumy_lsa", "sumy_lsa_regex")):
        raise RuntimeError(f"Actual LSA unavailable: {english.method}")
    print("OK: standalone Streamlit UI, default sources, actual EN/RU LSA.")
    print("No external API requests were made.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

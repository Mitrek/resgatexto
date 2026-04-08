# Resgatexto

**Resgatexto** is a Python-based background utility that acts as a system-wide AI text enhancer. It allows you to select any text on your computer and press a hotkey to instantly proofread, clarify, and format it into a formal tone using OpenAI's API. 

The script runs silently in the system tray and features a minimalist GUI for toggling the service and customizing the AI prompt.

## ✨ Features

* **Global Hotkeys:** Works across any application on your OS.
    * `F8`: Copies the selected text, processes it through the AI, and saves the improved version to your clipboard.
    * `F9`: Does the same as F8, but automatically **pastes** the improved text, instantly replacing your original selection.
* **System Tray Integration:** Runs quietly in the background with a system tray icon to access the settings panel, clear the cache, or exit.
* **Built-in Caching:** Remembers previously processed text (up to 200 items) to save API calls, reduce latency, and save money.
* **Customizable AI Prompt:** A Tkinter-based GUI allows you to rewrite the system prompt on the fly to change how the AI behaves (e.g., translate text, write code, or change the tone).
* **Safety & Validation Guardrails:** Prevents accidental API spam by enforcing a 3-second cooldown, character/word/token limits, and checking if the clipboard actually changed.

## 🛠️ Prerequisites

To run this script, you will need Python installed on your system along with several third-party libraries. 

1. Install the required dependencies:
```bash
pip install pyperclip keyboard pyautogui openai pystray pillow
```
*(Note: `tkinter` and `threading` are included in Python's standard library).*

2. **OpenAI API Key:** Ensure your environment has your OpenAI API key configured, as the `OpenAI()` client automatically looks for the `OPENAI_API_KEY` environment variable.

## 🚀 How to Use

1. **Run the script:** Start the Python script. The GUI will be hidden by default, and a new icon will appear in your system tray.
2. **Select Text:** Highlight a block of text in any app (browser, Word, Notepad, etc.).
3. **Trigger the AI:**
    * Press **`F8`** to silently copy and process the text. You can then `Ctrl+V` manually wherever you want.
    * Press **`F9`** to process the text and automatically replace the highlighted text with the AI's response.
4. **Open the GUI:** Right-click the system tray icon and click "Abrir painel" (Open panel). Here you can:
    * Toggle the daemon ON/OFF.
    * Edit the system prompt instructing the AI on how to handle your text.
    * Monitor cache size and the time of the last API call.

## ⚙️ Configuration Variables

If you need to tweak the script's core behavior, look for the `# CONFIG` section at the top of the file:

* `MODEL`: The OpenAI model used (Default: `"gpt-4.1-nano"`).
* `MIN_WORDS` / `MAX_WORDS`: Ignores selections outside this word count range.
* `MIN_CHARS` / `MAX_CHARS`: Ignores selections outside this character count range.
* `CACHE_MAX`: Maximum number of prompts to keep in memory (Default: `200`).
* `COOLDOWN_SECONDS`: Time to wait between API calls to prevent spam (Default: `3`).
* `TIMEOUT_SECONDS`: Max time to wait for an OpenAI response (Default: `8`).

## 🧠 Default Behavior

By default, the script instructs the AI (in Portuguese) to act as a text reviewer. It enforces strict rules:
* Improve clarity and make the tone formal/technical.
* Never invent information, add new dates/names, or leave comments.
* Output *only* the raw text (no Markdown, no quotes, no lists).

***

import os
from datetime import datetime

ROOT_DIR = "."
OUTPUT_FILE = "codebase_export.txt"

# Directories to completely ignore (noise, binaries, dbs, environments)
IGNORE_DIRS = {
    "__pycache__", ".git", ".pytest_cache", "venv", "env", 
    "python312", "logs", "data", "models", ".gemini", ".vscode",
    "static" # Usually images/fonts, though we'll allow it if you have css/js
}

# Only include these text/code files
ALLOWED_EXTENSIONS = {
    ".py", ".html", ".yaml", ".yml", ".json", ".md", ".txt", ".css", ".js"
}

def generate_tree(dir_path, prefix=""):
    tree_str = ""
    try:
        entries = sorted(os.listdir(dir_path))
    except PermissionError:
        return ""

    # Filter out ignored and hidden folders
    entries = [e for e in entries if e not in IGNORE_DIRS and not e.startswith('.')]
    
    # Filter files by extension for the tree
    valid_entries = []
    for e in entries:
        full_path = os.path.join(dir_path, e)
        if os.path.isdir(full_path):
            valid_entries.append(e)
        else:
            ext = os.path.splitext(e)[1].lower()
            if ext in ALLOWED_EXTENSIONS or e in ["Dockerfile", ".env.example"]:
                # Exclude this script and the output file itself
                if e not in ["export_codebase.py", OUTPUT_FILE]:
                    valid_entries.append(e)

    for i, entry in enumerate(valid_entries):
        path = os.path.join(dir_path, entry)
        is_last = (i == len(valid_entries) - 1)
        connector = "└── " if is_last else "├── "

        if os.path.isdir(path):
            tree_str += f"{prefix}{connector}{entry}/\n"
            extension_prefix = "    " if is_last else "│   "
            tree_str += generate_tree(path, prefix + extension_prefix)
        else:
            tree_str += f"{prefix}{connector}{entry}\n"

    return tree_str

def export_codebase():
    with open(OUTPUT_FILE, "w", encoding="utf-8") as out:
        out.write(f"# Codebase Export Generated at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        out.write("# Project: MyUPI Voice+Action Prototype\n\n")

        out.write("## 📌 PROJECT CONTEXT & GOALS\n")
        out.write("This codebase is a **prototype/demo application** built to showcase a voice-and-text-driven AI assistant for UPI (Unified Payments Interface) operations. ")
        out.write("The goal is to demonstrate how an agentic AI architecture can seamlessly handle complex banking intents (e.g., pausing mandates, raising chargebacks, replaying transactions) ")
        out.write("using a pipeline of both deterministic rules and LLM-driven semantic intelligence.\n\n")

        out.write("## ⚠️ NOTE FOR AI REVIEWER\n")
        out.write("This is **STRICTLY A PROTOTYPE** built rapidly for an internal manager/leadership demo. Please keep this in mind during your review:\n")
        out.write("- **Not Production:** The database is a local SQLite file seeded with mock data. Real bank integrations, NPCI switches, and cryptography are intentionally mocked via UI overlays (simulating app screens).\n")
        out.write("- **LLM Integration:** The AI layer uses an OpenAI-compatible adapter connected to a hosted vLLM endpoint (Qwen2.5).\n")
        out.write("- **Focus Areas:** Please focus your review on the **Agentic Pipeline Architecture** (`orchestrator.py`, `agents/`), the state machine logic, and the UI/UX frontend integration, rather than production compliance/security.\n\n")

        # 1. Write Directory Structure
        out.write("## DIRECTORY STRUCTURE\n")
        out.write("```text\n")
        out.write(".\n")
        out.write(generate_tree(ROOT_DIR))
        out.write("```\n\n")

        out.write("## FILE CONTENTS\n\n")

        # 2. Walk and write file contents
        for root, dirs, files in os.walk(ROOT_DIR):
            # Modify dirs in-place to skip ignored directories
            dirs[:] = [d for d in dirs if d not in IGNORE_DIRS and not d.startswith('.')]

            for file in files:
                ext = os.path.splitext(file)[1].lower()
                
                # Skip sensitive, environment, or output files
                if file in [".env", ".env.example", "codebase_export.txt", "export_codebase.py"]:
                    continue

                # Check if file is allowed
                if ext in ALLOWED_EXTENSIONS or file in ["Dockerfile"]:
                    file_path = os.path.join(root, file)

                    try:
                        with open(file_path, "r", encoding="utf-8") as f:
                            content = f.read()

                        rel_path = os.path.relpath(file_path, ROOT_DIR).replace("\\", "/")
                        lang = ext[1:] if ext else 'text'
                        if lang == 'yml': lang = 'yaml'
                        
                        out.write(f"<!-- ======================================================== -->\n")
                        out.write(f"<!-- FILE: {rel_path} -->\n")
                        out.write(f"<!-- ======================================================== -->\n")
                        out.write(f"```{lang}\n")
                        out.write(content)
                        if not content.endswith("\n"):
                            out.write("\n")
                        out.write("```\n\n")
                    except Exception as e:
                        out.write(f"<!-- Error reading {file_path}: {e} -->\n\n")

    print(f"SUCCESS: Codebase exported to {OUTPUT_FILE}")

if __name__ == "__main__":
    export_codebase()

import os

# Set your target directory and the file extensions you care about
TARGET_DIR = "." 
ALLOWED_EXTENSIONS = {".py", ".cs", ".gd", ".js", ".ts", ".java", ".cpp", ".h", ".json", ".yaml", ".yml", ".xml", ".txt"}
OUTPUT_FILE = "gemini_context_dump.txt"
IGNORE_DIRS = {"venv", ".git", "__pycache__", ".godot", "node_modules", "dist", "build", "bin", , ".ctags.d"}

# tiny script that walks your project directory, grabs the text of every single .cs or .py file, and compiles it into one massive project_dump.txt file. This is the file you will feed into Gemini to give it the full context of your project. You can customize the TARGET_DIR and ALLOWED_EXTENSIONS variables to fit your needs. Just run this script, and it will create a gemini_context_dump.txt file with all your code in it, organized by file path for easy reference.
with open(OUTPUT_FILE, "w", encoding="utf-8") as outfile:
    for root, dirs, files in os.walk(TARGET_DIR):
        # Mutate dirs in-place to skip ignored directories
        dirs[:] = [d for d in dirs if d not in IGNORE_DIRS]
        
        for file in files:
            ext = os.path.splitext(file)[1]
            if ext in ALLOWED_EXTENSIONS:
                filepath = os.path.join(root, file)
                
                # Write a clear Markdown header for the AI
                outfile.write(f"\n{'='*60}\n")
                outfile.write(f"FILE: {filepath}\n")
                outfile.write(f"{'='*60}\n\n")
                
                try:
                    with open(filepath, "r", encoding="utf-8") as infile:
                        # Enumerate automatically tracks line numbers starting at 1
                        for line_num, line in enumerate(infile, start=1):
                            outfile.write(f"{line_num:4} | {line}")
                        outfile.write("\n")
                except Exception as e:
                    outfile.write(f"Error reading file: {e}\n")

print(f"Context compiled successfully into {OUTPUT_FILE}")
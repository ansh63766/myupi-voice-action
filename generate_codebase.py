import os

EXCLUDE_DIRS = {'venv', '__pycache__', '.git', 'data', '.idea'}
ALLOWED_EXTS = {'.py', '.html', '.js', '.md'}

def generate_codebase():
    with open('complete_codebase.txt', 'w', encoding='utf-8') as out:
        for root, dirs, files in os.walk('.'):
            dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
            for file in files:
                ext = os.path.splitext(file)[1]
                if ext in ALLOWED_EXTS and file not in ('complete_codebase.txt', 'generate_codebase.py'):
                    path = os.path.join(root, file)
                    out.write(f'================================================\n')
                    out.write(f'File: {path}\n')
                    out.write(f'================================================\n')
                    try:
                        with open(path, 'r', encoding='utf-8') as f:
                            out.write(f.read())
                    except Exception as e:
                        out.write(f'<Error reading file: {e}>\n')
                    out.write('\n\n')
    print('Done writing complete_codebase.txt')

if __name__ == '__main__':
    generate_codebase()

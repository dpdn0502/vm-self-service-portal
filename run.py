import sys
import io

# Force UTF-8 output on Windows (avoids UnicodeEncodeError for non-ASCII chars)
if sys.stdout.encoding != 'utf-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if sys.stderr.encoding != 'utf-8':
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

from app import create_app

app = create_app()

if __name__ == '__main__':
    app.run(debug=True)
"""
Corrige imports que foram reescritos (ex: por auto-organize do VS Code/Pylance)
de `from bot.X import Y` / `import bot.X` para `from X import Y` / `import X`.

O código do bot assume que `bot/` é a raiz de execução (roda com
`python -m db.init_db`, `python main.py`, etc de dentro da pasta bot/),
então imports internos nunca devem ter o prefixo `bot.`.

Uso (a partir da pasta bot/ ou da raiz do projeto):
    python fix_imports.py
"""
from __future__ import annotations

import re
from pathlib import Path

# Descobre a pasta bot/ a partir de onde o script está ou é chamado
candidates = [Path.cwd() / "bot", Path.cwd()]
bot_dir = next((c for c in candidates if (c / "agents").is_dir() and (c / "core").is_dir()), None)

if bot_dir is None:
    raise SystemExit(
        "Não encontrei a pasta bot/ (com subpastas agents/ e core/). "
        "Rode este script de dentro de 'ivanvestai' (raiz do projeto) ou de dentro de 'bot/'."
    )

pattern_from = re.compile(r"^(from )bot\.(\S+)(\s+import\s+.+)$", re.MULTILINE)
pattern_import = re.compile(r"^(import )bot\.(\S+)(.*)$", re.MULTILINE)

changed_files = []

for py_file in bot_dir.rglob("*.py"):
    original = py_file.read_text(encoding="utf-8")
    fixed = pattern_from.sub(r"\1\2\3", original)
    fixed = pattern_import.sub(r"\1\2\3", fixed)
    if fixed != original:
        py_file.write_text(fixed, encoding="utf-8")
        changed_files.append(py_file.relative_to(bot_dir))

if changed_files:
    print(f"Corrigidos {len(changed_files)} arquivo(s):")
    for f in changed_files:
        print(f"  - {f}")
else:
    print("Nenhum import com prefixo 'bot.' encontrado — nada para corrigir.")

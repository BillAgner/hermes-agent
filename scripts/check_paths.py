from pathlib import Path
import sys, os
# Mimic what happens in install: _resolve_lock_install_path calls target.resolve()
target = Path('C:/Data/Hermes_0.17.0/skills/stocks')
skills_root = Path('C:/Data/Hermes_0.17.0/skills')
print('target:', target)
print('skills_root:', skills_root)
print('target.resolve():', target.resolve())
print('skills_root.resolve():', skills_root.resolve())
print('After resolve, is_relative_to:', target.resolve().is_relative_to(skills_root.resolve()))
print('---now simulate what install does---')
resolved_target = target.resolve()
resolved_root = skills_root.resolve()
print('Trying resolved_target.relative_to(resolved_root):')
try:
    print('  OK:', resolved_target.relative_to(resolved_root))
except Exception as e:
    print('  ERR:', e)

# Try the way install_from_quarantine does it
# install_dir = SKILLS_DIR / "skills" / "stocks" then resolve
# skills_root = SKILLS_DIR.resolve()
print('---test SKILLS_DIR via env---')
skills_root2 = Path(os.path.join('C:\\Data\\Hermes_0.17.0', 'skills'))
print('skills_root2:', skills_root2)
print('skills_root2.resolve():', skills_root2.resolve())
print('After resolve, is_relative_to:', target.resolve().is_relative_to(skills_root2.resolve()))
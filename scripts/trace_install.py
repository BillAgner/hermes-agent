import os, sys
os.environ['HERMES_HOME'] = r'C:\Data\Hermes_0.17.0'
os.environ.pop('PYTHONPATH', None)
sys.path.insert(0, r'C:\Data\Hermes_0.17.0')

# Patch relative_to to log details
from pathlib import Path
orig_relative_to = Path.relative_to
def traced_relative_to(self, *other):
    try:
        result = orig_relative_to(self, *other)
        return result
    except ValueError as e:
        with open(r'C:\Data\Hermes_0.17.0\relative_to_failures.log', 'a') as f:
            f.write(f'\n--- FAILURE ---\n')
            f.write(f'self={self!r}\n')
            f.write(f'str(self)={str(self)!r}\n')
            f.write(f'str(self).lower()={str(self).lower()!r}\n')
            f.write(f'other={other!r}\n')
            f.write(f'str(other[0])={str(other[0])!r}\n')
            f.write(f'str(other[0]).lower()={str(other[0]).lower()!r}\n')
            f.write(f'self.is_relative_to(other[0])={self.is_relative_to(other[0])}\n')
            f.write(f'self.resolve()={self.resolve()!r}\n')
            f.write(f'other[0].resolve()={other[0].resolve()!r}\n')
            f.write(f'os.path.commonpath={os.path.commonpath([str(self), str(other[0])])}\n')
            f.write(f'os.path.relpath={os.path.relpath(str(self), str(other[0]))}\n')
        raise
Path.relative_to = traced_relative_to

# Clear log first
open(r'C:\Data\Hermes_0.17.0\relative_to_failures.log', 'w').close()

from hermes_cli.skills_hub import do_install
do_install('official/finance/stocks', force=True, skip_confirm=True)
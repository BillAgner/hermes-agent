import os, sys
os.environ['HERMES_HOME'] = r'C:\Data\Hermes_0.17.0'
os.environ.pop('PYTHONPATH', None)
sys.path.insert(0, r'C:\Data\Hermes_0.17.0')
from tools.skills_hub import _resolve_lock_install_path
print('Resolved file:', _resolve_lock_install_path.__code__.co_filename)
print('Module:', _resolve_lock_install_path.__module__)
# Also check which one hermes_cli imports
import hermes_cli.skills_hub as cli_hub
print('hermes_cli.skills_hub file:', cli_hub.__file__)
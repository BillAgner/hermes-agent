import os, sys
os.environ['HERMES_HOME'] = r'C:\Data\Hermes_0.17.0'
os.environ.pop('PYTHONPATH', None)
sys.path.insert(0, r'C:\Data\Hermes_0.17.0')

from hermes_cli.skills_hub import do_install
try:
    do_install('official/finance/stocks', force=True, skip_confirm=True)
except SystemExit as e:
    print('SystemExit:', e.code)
except Exception as e:
    print('ERR:', type(e).__name__, e)
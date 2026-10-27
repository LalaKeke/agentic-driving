"""Install the pinned official CLI under this project; never update global npm."""
import json
from pathlib import Path
import shutil
import subprocess

if __name__=='__main__':
    here=Path(__file__).resolve().parent
    root=here.parents[2];target=root/'runs/tools/codex-0.164.0';target.mkdir(parents=True,exist_ok=True)
    shutil.copy2(here/'codex_client.package.json',target/'package.json')
    shutil.copy2(here/'codex_client.package-lock.json',target/'package-lock.json')
    command=['npm','ci','--prefix',str(target),'--cache','/tmp/agentic-driving-npm-cache',
             '--ignore-scripts','--no-audit','--no-fund','--registry','https://registry.npmjs.org']
    subprocess.run(command,check=True)
    result=subprocess.run([str(target/'node_modules/.bin/codex'),'--version'],capture_output=True,text=True,check=True)
    print(json.dumps(dict(cli_version=result.stdout.strip(),install_prefix=str(target),global_install_modified=False)))

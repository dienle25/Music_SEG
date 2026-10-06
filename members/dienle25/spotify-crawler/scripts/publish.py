"""Push module đã kiểm thử vào Music_TMG; không tạo repo hoặc force push."""
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent


def main():
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8')
    env = os.environ.copy()
    gh = shutil.which('gh')
    if not gh:
        for parent in ROOT.parents:
            candidate = parent / 'work/gh/bin/gh.exe'
            if candidate.is_file():
                gh = str(candidate)
                env['GH_CONFIG_DIR'] = str(parent / 'work/gh-config')
                break
    if not gh:
        print('Cần GitHub CLI đã xác thực để push.')
        return 1
    env.update(GIT_TERMINAL_PROMPT='0', GCM_INTERACTIVE='never', GH_NO_UPDATE_NOTIFIER='1')
    def run(*args, cwd=REPO):
        return subprocess.run(args, cwd=cwd, env=env, capture_output=True, text=True,
                              encoding='utf-8', errors='replace', timeout=120)
    origin = run('git', 'remote', 'get-url', 'origin')
    if origin.returncode or origin.stdout.strip() != 'https://github.com/dienle25/Music_TMG.git':
        print('Dừng: origin không khớp repository Music_TMG.')
        return 1
    branch = run('git', 'branch', '--show-current')
    status = run('git', 'status', '--porcelain')
    if branch.stdout.strip() != 'main' or status.returncode or status.stdout.strip():
        print('Dừng: cần branch main và đã commit các thay đổi.')
        return 1
    code = "import sys,unittest; sys.path.insert(0,'.'); unittest.main(module=None,argv=['unittest','discover','-s','tests'])"
    tests = run(sys.executable, '-c', code, cwd=ROOT)
    if tests.returncode:
        print('Dừng: tests chưa đạt.')
        return 1
    helper = '!"' + Path(gh).as_posix() + '" auth git-credential'
    result = run('git', '-c', 'credential.helper=', '-c', 'credential.helper=' + helper,
                 '-c', 'http.sslBackend=openssl', 'push', 'origin', 'main')
    if result.returncode:
        print('Push chưa thành công; kiểm tra xác thực và trạng thái remote.')
        return 1
    head = run('git', 'rev-parse', 'HEAD').stdout.strip()
    remote = run(gh, 'api', 'repos/dienle25/Music_TMG/git/ref/heads/main', '--jq', '.object.sha')
    if remote.returncode or remote.stdout.strip() != head:
        print('Chưa xác minh được SHA trên GitHub.')
        return 1
    print('GITHUB PUSH: SUCCESS - https://github.com/dienle25/Music_TMG - ' + head)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

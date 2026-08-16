"""XQuant 课程环境检查脚本

使用方法：在激活虚拟环境后运行
    python check_env.py
"""

import sys
import shutil


REQUIRED_PYTHON = (3, 12)

PACKAGES = [
    ("jupyter", "jupyter", "uv pip install jupyter==1.1.*"),
    ("pandas", "pandas", "uv pip install pandas==3.0.*"),
    ("numpy", "numpy", "uv pip install numpy==2.4.*"),
    ("matplotlib", "matplotlib", "uv pip install matplotlib==3.10.*"),
    ("akshare", "akshare", "uv pip install akshare==1.18.*"),
    ("yfinance", "yfinance", "uv pip install yfinance==1.2.*"),
    ("oxq", "open-xquant",
     'uv pip install "open-xquant @ git+https://github.com/xingwudao/open-xquant.git@6af7fa77f143e2f5dfc9d96464f9f60b66b4c60a"'),
]

ACTIVATE_HINT = (
    ".venv\\Scripts\\activate" if sys.platform == "win32"
    else "source .venv/bin/activate"
)


def check_python():
    v = sys.version_info
    ok = (v.major, v.minor) >= REQUIRED_PYTHON
    msg = f"Python {v.major}.{v.minor}.{v.micro}"
    if not ok:
        msg += f"（需要 >= {REQUIRED_PYTHON[0]}.{REQUIRED_PYTHON[1]}）"
    return ok, msg


def check_venv():
    in_venv = sys.prefix != sys.base_prefix
    if in_venv:
        return True, "虚拟环境已激活"
    return False, f"虚拟环境未激活；运行 {ACTIVATE_HINT}"


def check_pkg(import_name, display, install_cmd):
    try:
        __import__(import_name)
        return True, display
    except ImportError:
        return False, f"{display} 未安装；运行: {install_cmd}"


def check_jupyter_cmd():
    if shutil.which("jupyter"):
        return True, "jupyter 命令可用"
    return False, "jupyter 命令不可用；运行: uv pip install jupyter==1.1.*"


def check_akshare_data():
    try:
        import akshare as ak
        df = ak.fund_etf_hist_em(symbol="510300", period="daily", adjust="qfq")
        if len(df) > 0:
            return True, f"akshare 数据源连通（获取到 {len(df)} 条数据）"
        return False, "akshare 返回空数据，可能接口变动"
    except ImportError:
        return False, "akshare 未安装；跳过数据源测试"
    except Exception as e:
        return False, f"akshare 数据获取失败: {e}"


def main():
    print()
    print("=" * 50)
    print("  XQuant 课程环境检查")
    print("=" * 50)
    print()

    results = [check_python(), check_venv()]
    for name, display, install in PACKAGES:
        results.append(check_pkg(name, display, install))
    results.append(check_jupyter_cmd())
    results.append(check_akshare_data())

    passed = sum(1 for ok, _ in results if ok)
    total = len(results)

    for ok, msg in results:
        print(f"  [{'OK  ' if ok else 'FAIL'}] {msg}")

    print()
    print("-" * 50)
    if passed == total:
        print(f"  结果: {total}/{total} 全部通过！")
        print("  环境配置完成，可以开始课程。")
    else:
        print(f"  结果: {passed}/{total} 通过，{total - passed} 项需修复")
        print("  把上面输出复制给 AI 助手，让它帮你排查。")
    print()
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
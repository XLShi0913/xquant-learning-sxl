powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
uv --version

uv venv --python 3.12
.venv\Scripts\activate
python --version

uv pip install --index-url https://pypi.tuna.tsinghua.edu.cn/simple `
  jupyter==1.1.* pandas==3.0.* numpy==2.4.* matplotlib==3.10.* `
  akshare==1.18.* yfinance==1.2.*

uv pip install "open-xquant @ git+https://github.com/xingwudao/open-xquant.git@6af7fa77f143e2f5dfc9d96464f9f60b66b4c60a"
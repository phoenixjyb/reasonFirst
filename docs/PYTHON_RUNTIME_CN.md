# 明确选择项目 Python：Windows 与其他本机环境

[English](PYTHON_RUNTIME.md) · [安装](INSTALL_CN.md) · [故障排查](TROUBLESHOOTING_CN.md)

项目可能要求 `python3`，而 Windows 只有 `python.exe`；普通 PowerShell 可见的
用户级 Python，在 worker 的执行环境中也可能无法通过 launcher 找到。不要因此
重装 Python、放宽沙箱、修改全局 PATH 或改写受保护的项目契约。

## 为已有工作区确认一次解释器

选择项目本来需要的环境，不自动替换成 ReasonFirst 工具环境中的 Python。
若无第三方依赖的项目明确使用已经安装的系统 Python 3.12：

```powershell
$workspace = Read-Host "已有 ReasonFirst 工作区 ID"
actual-coder python bind $workspace --command python3 --python 3.12
```

该命令通过 uv 离线查找已安装的系统解释器，不发现项目或配置文件，不下载 Python，
也不调用 `py`。先核对显示的路径及工作区，再同意受限探测并保存绑定。使用项目
虚拟环境时，改用下面的方式明确选择，不要把两种方式连续执行：

```powershell
$interpreter = Read-Host "项目现有 Python 可执行文件的绝对路径"
actual-coder python bind $workspace --command python3 --executable $interpreter
```

只支持显式的 `python` 或 `python3` 映射，原命令必须已经被用户的可执行文件白名单
授权。非交互调用需显式 `--yes`；覆盖现有绑定需 `--replace` 并重新确认。未确认
不保存。版本发现可能检查已有解释器；选中可执行文件的固定身份探测在确认后进行，
在临时目录中运行，限制时间与读取输出，不导入项目或 site 启动代码。

记录保存在工作区以外的 `python-bindings` 中，绑定工作区、项目、固定 base、
GitLab 实例及平台，包含非密钥的解释器身份和文件指纹。不修改 TaskSpec、
`.actualcoder.yaml`、GitLab 密钥配置、Git 设置或 Windows 凭据。
保留虚拟环境的调用路径，不能把 python 符号链接解析成基础解释器路径后再调用。
二进制、链接目标或 `pyvenv.cfg` 变化时，在再次探测/运行之前要求重新审批。
身份比较允许父目录别名规范化（包括 macOS 临时目录别名），但不会因为两个虚拟
环境的 Python 链接指向同一二进制就将它们视为相同环境。仍保留已确认的调用路径；
父目录链接改指其他目录时要求重新审批。早期草案创建的不含目录指纹的绑定也需重新审批。

## 执行原有固定契约

```powershell
actual-coder python status $workspace
actual-coder validate $workspace --plan
actual-coder validate $workspace
```

status/plan 校验已确认的解释器身份及命令可用性，不运行项目测试。validate 执行
固定 base 的契约，不使用工作副本中可能改写的契约。原始 `python3 -m unittest ...`
不变，仅将 argv 的首项映射到已确认的绝对路径；其余参数原样保留。
输出同时包含请求/实际 argv、解释器依据、超时与真实退出码。没有测试命令不算通过；
解析失败先停止，必需测试失败仍然阻断。命令不启动 worker，不 commit/push；
测试本身可以写文件，仍受原有本机命令执行策略约束。

绑定的 Python 执行不继承 Python home/path 及 launcher 身份覆盖
（`PYTHONEXECUTABLE`、`__PYVENV_LAUNCHER__`），使用 UTF-8 管道输出并禁止生成
字节码缓存；不关闭断言、不跳过测试、不更换参数或安装依赖。finish 使用同一解析
实现，并将绑定写入审阅快照；修改绑定会使此前的发布计划失效。

## Worker 与证据边界

绑定后 resume 同一工作区。CLI 和本地 Bridge 的 handoff 会附带原始/实际命令和
已确认身份。worker 必须在已有沙箱中再次验证该解释器；宿主探测成功不等于沙箱
可访问。失败时停止，不放宽权限或换解释器。旧任务若明确要求另一解释器，需人类
澄清，不自动改写。没有新增能修改绑定的 MCP 工具，SSH/容器目标不继承本机路径。

确定性 runner 仍是结构化本机执行器，不是文件系统沙箱。用户侧记录与指纹不防御
同用户恶意代码；二进制与 venv 配置指纹不等于依赖包、DLL 或 site 定制的完整证明。
身份探测也不证明依赖齐全。worker 策略、网络策略、项目权限、发布审批分别验收。
公开问题报告应去除本机路径。

运行中的隧道仍使用原安装包时，通过已审阅 wheel 的 `uv tool run --isolated --from`
路线验证候选，不覆盖正在使用的环境。这里只隔离软件包，不隔离工作区：绑定有意
保存在已有工作区的本地管理目录，以便下一次继续使用。

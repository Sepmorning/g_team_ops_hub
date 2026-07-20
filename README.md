# FBA物流查询与WPS回填

Windows桌面程序，支持安达、超鸿和易通物流查询，自动识别货代并提示多货代冲突；查询成功后可将发生变化的最新路由写入WPS共享表的 `US-FBA` 子表。

## 克隆后直接运行

仓库根目录包含已打包的Windows程序：

```text
AndaFbaTracker.exe
```

双击EXE或 `启动项目.bat` 即可运行，不需要另行安装Python依赖。`启动项目.bat` 只包含ASCII字符，可兼容代码页936。

> EXE未进行商业代码签名，Windows首次运行时可能显示安全提示。请只从本项目仓库下载。

## 源码开发

建议安装64位Python 3.12。首次建立项目内环境：

```powershell
.\setup_project.ps1
```

依赖会安装到项目自己的 `.venv`，不会使用全局Python环境。源码启动：

```powershell
.\.venv\Scripts\python.exe main.py
```

依赖清单：

- `requirements.txt`：运行依赖；
- `requirements-dev.txt`：运行依赖、测试工具和EXE构建工具。

## 本机数据与安全

程序在根目录的 `data/app.db` 保存本机设置。安达、易通密码、易通会话令牌、WPS APPKEY和WPS授权令牌均使用Windows DPAPI加密，不会明文写入代码、配置或日志。

以下内容被Git排除，不会上传GitHub：

- `data/`及SQLite数据库；
- `.venv/`；
- 日志、缓存、临时打包目录；
- `.env`、密钥和证书文件。

换Windows用户或电脑后，DPAPI密文通常无法解密，需要重新输入账号和完成WPS授权。

## 易通首次使用

1. 输入自己的易通账号和密码。
2. 等待验证码图片出现并人工输入。
3. 点击“安全保存并登录”。
4. 有效会话会加密保存在本机；会话失效后才需要重新验证。

程序不会识别、破解或绕过图形验证码。

## WPS共享表

当前只处理名称为 `US-FBA` 的子表，不写入其他国家子表。

首次连接需要填写：

- APPID；
- APPKEY；
- WPS共享表链接；
- FBA所在列字母；
- 路由信息所在列字母。

列字母只需首次填写。指定列后，表头文字可以是 `FBA`、`FBA号`、`路由`、`货代最新路由信息` 或其他名称。

WPS传统 `.xlsx` 的查找接口会把第一行作为字段定义并排除，当前权限下文件下载接口也不可用，因此程序不能安全读取表头并自动选列。程序不会猜测路由列，避免写错业务数据。

同步规则：

- 每次同步前刷新 `US-FBA` 的最新使用范围；
- 只处理查询成功且存在最新路由的FBA；
- 只修改指定的路由信息列，不修改其他列和格式；
- 新旧路由完全相同时不重复写入；
- 表内不存在的FBA跳过；
- 同一FBA出现多行时停止更新并提示重复。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q anda_tracker main.py tests
```

## 重新生成EXE

```powershell
.\build_exe.ps1
```

生成结果为根目录的 `AndaFbaTracker.exe`。打包临时目录会自动清理。

## 项目结构

```text
anda_fba_tracker/
├─ anda_tracker/         程序源码
├─ tests/                自动化测试
├─ data/                 本机加密数据库（不上传Git）
├─ AndaFbaTracker.exe    可直接运行的Windows程序
├─ 启动项目.bat           兼容代码页936的启动入口
├─ main.py               源码入口
├─ requirements.txt      运行依赖
├─ requirements-dev.txt  测试和构建依赖
├─ setup_project.ps1     建立项目内环境
├─ build_exe.ps1         生成EXE
└─ README.md
```

后续前后端和多页面开发约束记录在 `后续开发约束.md`。

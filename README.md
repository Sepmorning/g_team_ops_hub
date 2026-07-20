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

程序在根目录的 `data/app.db` 保存本机设置。安达、易通密码、易通会话令牌和AirScript脚本令牌均使用Windows DPAPI加密，不会明文写入代码、配置或日志。迁移前保存的WPS APPKEY和OAuth令牌也继续保持加密。

以下内容被Git排除，不会上传GitHub：

- `data/`及SQLite数据库；
- `.venv/`；
- 日志、缓存、临时打包目录；
- `.env`、密钥和证书文件。

换Windows用户或电脑后，DPAPI密文通常无法解密，需要重新输入账号和AirScript脚本令牌。

## 易通首次使用

1. 输入自己的易通账号和密码。
2. 等待验证码图片出现并人工输入。
3. 点击“安全保存并登录”。
4. 有效会话会加密保存在本机；会话失效后才需要重新验证。

程序不会识别、破解或绕过图形验证码。

## WPS共享表与AirScript

当前只处理名称为 `US-FBA` 的子表，不写入其他国家子表。

项目使用WPS文档共享脚本完成回填。正式脚本位于：

```text
airscripts/FBA物流自动回填.js
```

首次配置：

1. 在共享表的“效率 → 高级开发 → AirScript脚本编辑器”中新建AirScript 2.0文档共享脚本。
2. 将正式脚本完整粘贴进去，保存并手动运行一次；默认运行只做验证，不写数据。
3. 从文档共享脚本菜单复制webhook。
4. 在程序中填写共享表链接、webhook和脚本令牌。
5. 点击“安全保存并测试AirScript”。

程序不再要求填写APPID、APPKEY或列字母。脚本扫描第一行前100列并自动识别：

- FBA列：`FBA单号`、`FBA号`、`FBA编号`或`FBA`；
- 路由列：`货代最新路由信息`、`最新路由信息`或`路由`。

目标表头不存在或出现多列时，脚本停止写入，不会猜测目标列。

同步规则：

- 每次同步重新识别 `US-FBA` 和目标表头；
- 只处理查询成功且存在最新路由的FBA；
- 只修改自动识别出的路由信息单元格，不修改其他列和格式；
- 新旧路由完全相同时不重复写入；
- 表内不存在的FBA跳过；
- 同一FBA出现多行时不更新该FBA并提示重复；
- 单次最多回填50条，脚本最多扫描到第20000行，避免异常使用区域造成长时间运行。

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
├─ airscripts/           需要安装到WPS文档共享脚本的正式脚本
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

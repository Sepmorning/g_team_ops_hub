# FBA运营工作台（网页版）

这是当前持续开发的FastAPI网页版项目。桌面验证版已经分离到同级目录 `D:\CodexProject\FBA_Tracker_Desktop`，两套程序拥有独立源码、依赖、数据库和EXE，互不读写对方的数据。

## 已实现功能

- 管理员初始化、普通用户创建、停用和密码重置
- 用户货代账号、登录会话和店铺配置严格按用户隔离
- 安达、超鸿、易通查询与货代冲突检测
- 多店铺WPS AirScript配置
- 手动FBA查询并可选回填
- 从 `US-FBA` 自动读取未完成FBA并分批查询、回填
- 本地滚动日志，不记录密码、令牌或请求正文

## 运行

直接运行：

```text
FbaTrackerWeb.exe
```

浏览器访问 `http://127.0.0.1:8765`。关闭EXE窗口会停止本机服务。

源码运行：

```powershell
.\setup_project.ps1
.\start_web.ps1
```

项目依赖全部安装在本目录的 `.venv` 中。本机业务数据位于 `data`，不会提交到版本库。

## 测试

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m compileall -q anda_tracker web_main.py tests
```

重新生成网页版EXE：

```powershell
.\build_web_exe.ps1
```

## 主要目录

```text
anda_fba_tracker/
├─ anda_tracker/
│  ├─ web/                 FastAPI路由、页面和静态资源
│  ├─ airscript.py         WPS AirScript客户端
│  ├─ auth.py              系统账号
│  ├─ storage.py           用户隔离的本地数据
│  ├─ client.py            安达
│  ├─ chaohong.py          超鸿
│  └─ yitong.py            易通
├─ airscripts/             安装到WPS的文档共享脚本
├─ tests/                  网页版和查询核心测试
├─ data/
│  ├─ app.db               网页版独立数据库
│  └─ logs/web.log         运行日志
├─ web_main.py             网页版入口
├─ start_web.ps1           源码启动脚本
├─ build_web_exe.ps1       EXE构建脚本
└─ FbaTrackerWeb.exe
```

## 数据和安全

- 登录密码使用不可逆PBKDF2哈希。
- 货代密码、登录令牌和AirScript令牌使用当前Windows用户的DPAPI加密。
- 管理员可以管理系统账号，但不能查看其他用户的业务密码或令牌明文。
- 每个店铺、货代配置和会话查询都必须带当前登录用户的 `profile_id`。
- `data` 目录只应保存在当前电脑；不要提交或发送给他人。

## AirScript

正式脚本为 `airscripts\FBA物流自动回填.js`。升级和必需表头见 `AirScript升级说明.md`。

任务总量不限制为50条。程序只在内部按最多50条分批查询和回填，以降低货代接口和WPS服务压力。

## API命名

- `/api/tracking/query`：手动物流查询
- `/api/carriers/status`：货代状态
- `/api/carriers/anda`：安达账号
- `/api/carriers/yitong/captcha-challenges`：易通验证码
- `/api/carriers/yitong/session`：易通登录会话
- `/api/shops`：店铺
- `/api/shops/{shop_id}/validation`：验证店铺AirScript
- `/api/shops/{shop_id}/tracking-sync`：一键查询并回填

旧的 `/api/connections/*`、`/api/query` 和单配置AirScript接口已经移除。

本轮发现、修复和保留风险详见 `项目代码审计报告.md`。

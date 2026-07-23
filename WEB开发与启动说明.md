# WEB开发与启动说明

## 当前结构

本目录只保留网页版：

- `FbaTrackerWeb.exe`：本机FastAPI后端，启动后自动打开浏览器。

桌面验证版已分离到 `D:\CodexProject\FBA_Tracker_Desktop`。两套项目的源码、依赖、数据库和EXE互相独立。

## 本机启动

最简单的方式是双击：

```text
FbaTrackerWeb.exe
```

启动后浏览器会自动打开：

```text
http://127.0.0.1:8765
```

运行期间请保持后端控制台窗口开启。关闭控制台即停止后端，浏览器页面随后无法继续查询。

源码开发方式：

```powershell
.\start_web.ps1
```

## 数据

网页版只使用：

```text
data/app.db
```

桌面版使用 `D:\CodexProject\FBA_Tracker_Desktop\data\app.db`。分离时复制了一份数据快照，之后两边不会自动同步。网页版还会生成：

```text
data/web_session.key
```

该文件保存的是Windows DPAPI密文，不是明文会话密钥。不要删除正在使用的 `data` 文件夹。

## 当前安全边界

当前Web服务只监听 `127.0.0.1`，只能从本机访问。不要通过端口转发、关闭防火墙或把监听地址改成 `0.0.0.0` 来直接给其他人使用，因为当前本机开发模式尚未配置HTTPS、反向代理、服务器备份和正式密钥管理。

## 后续正式给组员使用

正式多人版本建议：

1. 部署到公司所有或明确授权的Windows/Linux服务器；
2. 使用PostgreSQL替换SQLite；
3. 配置域名、HTTPS和反向代理；
4. 将DPAPI业务密钥迁移为服务器主密钥或密钥管理服务；
5. 增加登录失败锁定、数据库定时备份和集中审计；
6. 设置至少两个公司掌握的超级管理员；
7. 完成数据导出和管理员交接流程。

## 页面

- `/setup`：首次管理员初始化；
- `/login`：登录；
- `/dashboard`：工作台；
- `/tracking`：三家货代查询和共享表同步；
- `/carriers`：当前用户自己的货代账号和验证码登录；
- `/shops`：当前用户的多个店铺和AirScript共享表；
- `/inventory`：库存销售表格预留页面；
- `/admin`：管理员账号管理。

新版一键查询前必须按照 [AirScript升级说明.md](AirScript升级说明.md) 替换WPS中的文档共享脚本。

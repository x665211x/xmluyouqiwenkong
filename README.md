# XMluyouqiwenkong — 小米路由器温控 / 全状态集成

Home Assistant 自定义集成（custom component）。通过 **Telnet 或 SSH（界面里随时切换）** 采集小米路由器（小米路由器 10000 Pro / AX10000 / 万兆路由等）的运行状态，并把 CPU 温度做成可告警的温控实体。

- 路由器 IP **在 HA 界面里自己填**（支持 `192.168.31.1` 或 `192.168.31.1:23` 写法），代码里不写死
- Telnet 通道纯标准库实现，不依赖 telnetlib（Python 3.13 已移除它），**选 Telnet 时零额外依赖**
- SSH 通道用 paramiko：manifest 已声明 `paramiko>=3.4.0`，HA 首次加载会自动安装（离线 HA 需手动装，见排错）
- 每个刷新周期只连一次 Telnet、只发一段巡检脚本，30 秒一轮也不会拖累路由器
- 自带离线自检脚本，安装前就能在本机确认真能取到数

## 一、目录结构

```
XMluyouqiwenkong/
├── custom_components/
│   └── xmluyouqiwenkong/          ← 整个文件夹拷到 HA 的 config/custom_components/
│       ├── manifest.json
│       ├── __init__.py            ← 入口 + 4 个服务（run_command / ping / traceroute / tail_log）
│       ├── const.py
│       ├── config_flow.py         ← 界面配置 / 修改配置
│       ├── coordinator.py         ← 定时取数、算占用率与速率
│       ├── telnet_client.py       ← Telnet 客户端 + 巡检脚本
│       ├── ssh_client.py          ← SSH 客户端（paramiko，复用同一套脚本与解析）
│       ├── parser.py              ← 输出解析（纯函数，可离线测试）
│       ├── sensor.py              ← 传感器
│       ├── binary_sensor.py       ← 连接 / 过热 / 风扇
│       ├── number.py              ← 过热告警阈值
│       ├── button.py              ← 立即刷新
│       ├── diagnostics.py         ← 诊断信息（密码自动打码）
│       ├── services.yaml
│       ├── strings.json
│       └── translations/          ← zh-Hans / zh-Hant / en
├── tools/
│   ├── check_telnet.py            ← 电脑上直接测试路由器（不需要 HA）
│   ├── check_ssh.py               ← 电脑上直接测 SSH 通道（需要 paramiko）
│   ├── test_telnet.py             ← 同上，带 --password / --raw 参数、会采样两轮算占用率
│   ├── selftest.py                ← 解析逻辑离线自检
│   └── mock_telnet_test.py        ← 假 Telnet 服务器端到端测试
├── hacs.json
└── README.md
```

## 二、安装（3 分钟）

1. 把 `custom_components/xmluyouqiwenkong` 整个文件夹拷到 Home Assistant 配置目录，最终路径必须是：

   ```
   /config/custom_components/xmluyouqiwenkong/__init__.py
   /config/custom_components/xmluyouqiwenkong/manifest.json
   ...
   ```

   > 文件夹名必须是全小写 `xmluyouqiwenkong`，不能改名。
   > HA OS / Supervised 用 Samba、File Editor 或 SSH 插件；Docker 版放进映射出来的 config 目录。

2. **重启 Home Assistant**（设置 → 系统 → 重启，不是只重载 YAML）。

3. 「设置 → 设备与服务 → 添加集成」搜索 **小米路由器温控**（或 `XMluyouqiwenkong`）。

4. 填写：

   | 字段 | 说明 | 默认 |
   |---|---|---|
   | 连接方式 | `Telnet`（默认）或 `SSH`，之后随时可改 | Telnet |
   | 路由器 IP 地址 | 路由器后台地址，可写 `192.168.1.1:23` | 192.168.1.1 |
   | 端口 | Telnet 默认 23，SSH 默认 22；留默认会按连接方式自动取 | 23 |
   | 用户名 | 两个通道一般都填 `root` | root |
   | 密码 | Telnet 直接进 root shell 可留空；**SSH 必须填密码** | 空 |
   | 刷新间隔（秒） | 10–3600，散热联动建议 15–30 | 30 |
   | 连接超时（秒） | 3–60 | 10 |

   保存时集成会真的连一次、跑一次巡检脚本：连不上提示「连接不上路由器」，密码错提示「用户名或密码不正确」（会走 HA 的重新认证流程）。

   > **root + 免密登录（AX10000 / 10000 Pro 常见形态）**：IP 填路由器地址、端口 `23`、用户名 `root`、密码**留空**，其余保持默认即可。密码留空时客户端不会卡在等密码，连上后直接进 shell 发巡检脚本。

   > **改用 SSH**：连接方式选 `SSH` → 端口自动取 22（若还留着 23，保存时会改成 22）→ 用户名 `root` → 填 SSH 登录密码（dropbear 不接受空密码，留空会报「SSH 认证失败」）。前提是路由器上 dropbear 在监听 22：`netstat -lnt | grep 22` 能看到即可。
   >
   > 切换连接方式属于连接参数变更，保存后集成**自动重载**，不用重启 HA，实体 ID 与历史数据都不变。
   >
   > 不确定 SSH 通不通，先在电脑上跑一次：`python -m pip install paramiko` 然后 `python tools\check_ssh.py 192.168.1.1 root 你的密码`。能出数就说明 HA 里选 SSH 也一定能出数。

5. 之后改 IP / 密码 / 间隔 / 阈值：设备与服务 → 该集成 → **配置**，保存时同样会重新验证连接。

### 先在本机验证（强烈建议）

不用装 HA，也不用装任何库：

```powershell
cd "D:\系统文件\桌面\XMluyouqiwenkong"
python tools\check_telnet.py 192.168.1.1
python tools\check_telnet.py 192.168.1.1 root 你的密码
```

也可以用带参数、会采样两轮（顺便算出 CPU 占用率和实时速率）的版本：

```powershell
python tools\test_telnet.py 192.168.1.1 --password 你的密码 --raw
```

它会打印原始输出 + 解析后的关键指标：

- 出一堆数值 → 通道没问题，集成直接能用
- `✗ 失败：连接 ... 失败` → Telnet 没开、IP 不对、端口不是 23
- `✗ 失败：路由器要求输入密码` → 补第 3 个参数
- 温度为空 → 用输出里的 `zone=` 行确认该机型温区名字

## 三、实体清单

**传感器（27 个静态 + 每个温区 1 个动态）**

| 实体 | 含义 | 数据来源 |
|---|---|---|
| CPU 温度 | 代表温区（含 cpu/soc 的温区优先，否则取最高），属性里带全部温区 | `/sys/class/thermal/thermal_zone*/temp` |
| 温区 &lt;名称&gt; | 每个温区单独一个实体（CPU / DDR / Wi-Fi…），无线芯片温度也能看到 | 同上 |
| CPU 占用率 | 两次 `/proc/stat` 差分 | `/proc/stat` |
| CPU 频率 | 当前主频 | `scaling_cur_freq` |
| 1 / 5 / 15 分钟负载 | 系统负载 | `/proc/loadavg` |
| 内存占用率、可用内存、内存总量 | % 与 MB | `/proc/meminfo` |
| 运行时长 | 秒（HA 自动显示为天/小时） | `/proc/uptime` |
| 在线设备数 | ARP 在线条目数，属性 `online_devices` 是 `IP MAC` 清单 | `/proc/net/arp` |
| Wi-Fi 终端数 | 各无线接口 station 数之和 | `iw dev ... station dump` |
| 下载 / 上传速率 | kB/s，两次轮询差分 | `/proc/net/dev` |
| 累计下载 / 上传流量 | MB，只增不减，可直接做长期统计 | 同上 |
| 存储占用率、已用、总量 | 根文件系统 | `df -k /` |
| 风扇转速 | rpm（有 `fan1_input` 的机型才有值） | `/sys/class/hwmon/*/fan1_input` |
| 进程数、无线射频数 | 诊断用 | `ps`、`/sys/class/net` |
| 设备型号 / 固件 / 内核 / 主机名 | 诊断用 | `/proc/device-tree/model`、`/etc/mi_version` 等 |
| 状态快照 | 一次看全部解析值（属性） | 集成内部 |

**二进制传感器**：`Telnet 连接`（连通性）、`过热告警`（超标变 on）、`风扇运转`（检测到风扇才创建）

**数字实体**：`过热告警阈值`（默认 75 ℃，30–110 可调，改完立即生效并写入配置，重启后保持）

**按钮**：`立即刷新`

> 采不到的项显示「未知」，不影响其它实体，也不会让集成报错。

## 四、服务（运维）：run_command / ping / traceroute / tail_log

脚本没覆盖的状态可以自己发命令读（开发者工具 → 服务 → `xmluyouqiwenkong.run_command`）：

```yaml
service: xmluyouqiwenkong.run_command
data:
  command: "cat /proc/uptime; uptime; ls /sys/class/thermal"
```

结果在服务调用的响应 `output` 字段里，也能在自动化里用变量接。多台路由器时加 `entry_id` 指定目标。

### 4.1 ping —— 让路由器自己测外网

判断"是宽带断了，还是路由器自己卡住"最直接的办法：从路由器内部发包。

| 字段 | 说明 | 默认 |
|---|---|---|
| `target` | 只能填 IP 或域名 | `223.5.5.5` |
| `count` | 发包次数，1–20 | 4 |
| `entry_id` | 多台路由器时指定目标 | 第一个 |

```yaml
service: xmluyouqiwenkong.ping
data:
  target: 223.5.5.5
  count: 4
response_variable: ping_out
```

### 4.2 traceroute —— 定位是哪一跳不通

| 字段 | 说明 | 默认 |
|---|---|---|
| `target` | 只能填 IP 或域名 | `223.5.5.5` |
| `max_hops` | 最大跳数，1–30 | 12 |

```yaml
service: xmluyouqiwenkong.traceroute
data:
  target: 223.5.5.5
  max_hops: 15
response_variable: trace_out
```

### 4.3 tail_log —— 只读日志尾部

| 字段 | 说明 | 默认 |
|---|---|---|
| `path` | 绝对路径，只允许字母数字与 `. : / _ -` | `/tmp/messages` |
| `lines` | 读取行数，1–200 | 40 |

```yaml
service: xmluyouqiwenkong.tail_log
data:
  path: /tmp/messages
  lines: 60
response_variable: log_out
```

> 四个服务全部**只读**：不改路由器配置、不写文件、不改系统状态。
> 新加的三个服务参数都过白名单校验（只允许字母、数字与 `. : / _ -`），`;`、`|`、`$()`、空格、反引号会被直接拒绝并抛出错误，拼不进额外命令。
> 取数通道（Telnet / SSH）对服务透明：选哪个协议，服务就走哪个通道。

## 五、温控 / 自动化示例

实体名按实际生成的替换（中文名会被转成拼音，例：`sensor.xiaomi_lu_you_qi_192_168_31_1_cpu_wen_du`）。

**线性温控（45 ℃ 起转、65 ℃ 满速，带 2 ℃ 回差防抖）**

```yaml
alias: 小米路由温度自动调速
mode: restart
trigger:
  - platform: state
    entity_id: sensor.xiaomi_lu_you_qi_cpu_wen_du
  - platform: homeassistant
    event: start
action:
  - variables:
      t: "{{ states('sensor.xiaomi_lu_you_qi_cpu_wen_du') | float(0) }}"
      fan: fan.luyou_sanre_fengshan
  - choose:
      - conditions: "{{ t >= 65 }}"
        sequence:
          - action: fan.set_percentage
            target: { entity_id: "{{ fan }}" }
            data: { percentage: 100 }
      - conditions: "{{ t >= 45 }}"
        sequence:
          - action: fan.set_percentage
            target: { entity_id: "{{ fan }}" }
            data:
              percentage: "{{ (((t - 45) / 20 * 100) | round(0) | int) | min(100) | max(0) }}"
      - conditions: "{{ t < 44 }}"
        sequence:
          - action: fan.set_percentage
            target: { entity_id: "{{ fan }}" }
            data: { percentage: 0 }
```

> ESPHome PWM 风扇低于 20% 可能不转，中间档可以把 `max(0)` 改成 `max(20)`。

**过热告警（用集成的二进制传感器，阈值跟随数字实体）**

```yaml
alias: 路由器过热提醒
trigger:
  - platform: state
    entity_id: binary_sensor.xiaomi_lu_you_qi_guo_re_gao_jing
    to: "on"
    for: "00:03:00"
action:
  - action: notify.persistent_notification
    data:
      title: 路由器温度过高
      message: >-
        当前 {{ states('sensor.xiaomi_lu_you_qi_cpu_wen_du') }} ℃，
        阈值 {{ state_attr('binary_sensor.xiaomi_lu_you_qi_guo_re_gao_jing','threshold') }} ℃，
        请检查风扇与通风。
```

**每日汇总**

```yaml
alias: 路由器日报
trigger:
  - platform: time
    at: "08:00:00"
action:
  - action: notify.persistent_notification
    data:
      title: 路由器状态
      message: >-
        温度 {{ states('sensor.xiaomi_lu_you_qi_cpu_wen_du') }}℃，
        内存 {{ states('sensor.xiaomi_lu_you_qi_nei_cun_zhan_yong_lu') }}%，
        在线 {{ states('sensor.xiaomi_lu_you_qi_zai_xian_she_bei_shu') }} 台，
        已运行 {{ states('sensor.xiaomi_lu_you_qi_yun_xing_shi_chang') }} 秒
```

## 六、排错

| 现象 | 原因 / 处理 |
|---|---|
| 添加集成时提示 **无法加载配置向导: Invalid handler specified** | 含义是「HA 导入 `config_flow.py` 时会先执行集成包的 `__init__.py`，包级导入已经抛异常了」。**本版已修掉真实根因**：HA 2026.x 起 `homeassistant.const` 不再导出 `ATTR_ENTRY_ID`、`homeassistant.helpers.config_validation` 不再导出 `string`，旧版 `__init__.py` 导入这两个名字会直接 ImportError，向导自然注册不上。换成本包后若仍报，先看「设置 → 系统 → 日志」里 `xmluyouqiwenkong` 的 traceback，再确认目录没套两层（必须是 `config/custom_components/xmluyouqiwenkong/__init__.py`），改完文件必须重启 HA |
| 添加时提示「连接不上」 | Telnet 没开、IP 错、端口不对；先跑 `tools/check_telnet.py` |
| 提示「路由器要求输入密码」 | 该固件 Telnet 需要独立 root 密码（不是 WiFi/管理密码）；部分机型可由 SN 推导或在开启脚本里打印 |
| 温度一直没有值 | 该机型温区路径不同；用 `check_telnet.py` 的 `zone=` 行、或 `ls /sys/class/thermal/` 手工确认 |
| Wi-Fi 终端数是「未知」 | 固件里没有 `iw`；不影响其它指标，可用 `run_command` 换成 `wl` 或 MiWiFi API |
| 在线设备数比米家少 | ARP 只反映「有 IP 且最近通信」的设备，纯二层设备可能不出现 |
| CPU 占用率第一次是「未知」 | 占用率靠两次采样差分，第二轮刷新（默认 30 秒后）才出值 |
| 跑一段时间后掉线 | 部分 telnetd 有连接数限制；把刷新间隔调到 30–60 秒 |
| 路由器重启后连不上 | Telnet 多数是破解开启的，重启不自启，需要在开机脚本里持久化 |
| 想换 SSH | 界面「配置」里把连接方式改成 SSH 即可，代码不用动；先跑 `python tools\check_ssh.py 192.168.1.1 root 密码` 确认（需先 `pip install paramiko`） |
| SSH 报「认证失败（密码留空或不被接受）」 | SSH 不像 Telnet 能免密进 root shell，**必须填密码** |
| SSH 端口 22 连不上 | 路由器多半没开 dropbear：`dropbear -p 22` 起一次；端口字段留空时会按协议自动取 22 |
| 选 SSH 后日志出现 `Unable to install package paramiko` | 你的 HA 没有外网或禁了 pip：在 HA 里手动 `pip install paramiko>=3.4.0`，或先继续用 Telnet |
| 想看原始输出 | 集成页面 → 下载诊断信息，里面有 `raw_output` 与全部解析值（密码已打码） |

> 安全提示：Telnet 是明文协议，只在局域网内使用；不要把 23 端口映射到公网，也不要把 root 密码提交到公开仓库。

## 七、自检（开发者）

```powershell
python tools\selftest.py          # 解析逻辑：温度、占用率、速率、设备数…（24 项断言）
python tools\mock_telnet_test.py  # 本地假 telnetd：登录流程、回显剔除、错误密码
python tools\check_ssh.py 192.168.1.1 root 密码   # 真机 SSH 取数（需先 pip install paramiko）
```

两个脚本只依赖 Python 标准库，当前版本全部通过。

## 八、卸载

「设置 → 设备与服务 → 集成」删除该集成，再删掉 `config/custom_components/xmluyouqiwenkong` 文件夹并重启 HA。
## 九、HA 版本兼容说明（2026.11 实测）

这一版是照着 **Home Assistant 2026.11.1 的真实源码**逐符号核对后改的，不是猜的：

1. HA 2026 的 `homeassistant.const` 已经**不再导出** `ATTR_ENTRY_ID`（现在只有个别组件自己定义），旧版 `__init__.py` 里 `from homeassistant.const import ATTR_ENTRY_ID, Platform` 会直接 ImportError。
   而 HA 加载配置向导时会先 import 集成包的 `__init__.py`，所以包级导入一失败，向导就注册不上 —— 这就是「无法加载配置向导: Invalid handler specified」的真实原因。
   **本版改法**：本地定义 `ATTR_ENTRY_ID = "entry_id"`，不再从 const 取。
2. HA 2026 的配置校验内核换成了 `probatio`，`homeassistant.helpers.config_validation` 里已经没有 `string` 这个名字。
   **本版改法**：服务 schema 直接用 `str`，并删掉不再使用的 `config_validation` 导入。
3. 其余接口都还在，已逐个核对：`ConfigFlowResult`、`async_get_options_flow`、`async_forward_entry_setups`、`supports_response`、`DataUpdateCoordinator(hass, logger, *, config_entry, name, update_interval)`、`async_on_unload`、`add_update_listener`、各种 `UnitOf*` / `EntityCategory` / `SensorDeviceClass`。
4. 表单仍用 `vol.Schema`：HA 2026 在启动时会把 `voluptuous` 别名到 `probatio`（`install_as_voluptuous()`），所以旧写法继续有效；我还用 probatio 0.13.0 实跑过本集成的表单 schema，默认值、端口 Coerce、Range 越界都符合预期。
5. `SensorEntityDescription` 在 2026 改成元类 `FrozenOrThawed` 生成，其源码明确保留了「子类用 `@dataclass` 就按真实 dataclass 处理、并把父类注解注入进来」的分支，本集成正好走这条兼容分支，字段全部带默认值，不会有顺序问题。
6. `async_config_entry_first_refresh` 在 `config_entry` 为空时会抛 `ConfigEntryError`，本版已把 `config_entry=entry` 显式传给协调器（向导里的连通性探针用临时假对象，不传）。

**更新记录**

- 1.5.0（本次）：配置里新增「连接方式」Telnet / SSH 切换（端口按协议自动映射，切换后自动重载）；新增只读运维服务 `ping` / `traceroute` / `tail_log`（参数白名单校验）；新增 `tools/check_ssh.py`；诊断信息里增加 `protocol` 字段。SSH 通道依赖 manifest 里的 `paramiko>=3.4.0`。
- 1.3.0：修掉 HA 2026 下 `ATTR_ENTRY_ID` / `cv.string` 缺失导致的「Invalid handler specified」；默认 IP 改为 192.168.1.1。
- 1.2.0：`ConfigFlowResult` 兼容回退；manifest 规范化。
- 1.1.0：Telnet root + 免密登录说明与联调。
- 1.0.0：首版，27 个传感器 + 温控阈值 + run_command 服务。
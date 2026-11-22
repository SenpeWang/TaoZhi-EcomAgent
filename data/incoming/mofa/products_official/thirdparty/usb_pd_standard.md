# USB Power Delivery（USB PD）标准专题

- **专题入口**：https://www.usb.org/usb-charger-pd （USB-IF 官方）
- **采集时间**：2026-10-07
- **性质**：标准知识文档，来源于 USB 开发者论坛（USB Implementers Forum, usb.org）官方页面

## 1. 概述

USB 已从"可供电的数据接口"演变为"带数据能力的主要供电接口"。USB Power Delivery（USB PD）规范的目标是在**单根线缆上同时实现数据与更灵活的电力传输**，并与现有 USB 生态兼容运行。

## 2. PD 3.1 与 240W（2021 年发布）

- USB PD **Revision 3.1** 于 **2021 年**发布（USB-IF 官方公告：*USB PG USB PD 3.1 DevUpdate Announcement, 2021-05*）。
- 核心变化：通过**全功能（full-featured）USB Type-C® 线缆与连接器**将供电上限从 100W 提升至 **240W**。
- 此前限制：USB PD 上限 100W，基于 20V 方案 + 5A 额定 USB Type-C 线缆。
- 配套标准同步更新：**USB Type-C 规范 Release 2.1** 定义了 240W 线缆要求。
- 术语说明：业界常以 **EPR（Extended Power Range，扩展功率范围）** 指代 PD3.1 中 >100W 的工作模式；usb.org 本专题页未直接使用 EPR 缩写，EPR 的正式定义以 USB PD 规范原文为准（本页未展开，标注待核）。

## 3. USB PD 提供的能力（官方页明列）

1. **更高的功率等级**：最高 240W。
   - 新增 **28V、36V、48V 三档固定电压**，分别支持 **140W、180W、240W** 功率等级。
   - **AVS（Adjustable Voltage Supply，可调电压供电模式）**：受电设备可在 **15V 至充电器最高可用固定电压之间**请求中间电压。
2. **功率方向不再固定**：具备电力的一方（主机或外设）均可供电。
3. **多外设间优化功率管理**：各设备仅取用所需功率，应用需要时可请求更多。
4. **智能灵活的系统级功率管理**：可通过集线器与 PC 的可选通信实现。
5. **覆盖低功率场景**：如耳机等设备可只协商所需的小功率。

## 4. 典型应用场景（官方举例）

1. 通过全功能 USB Type-C 线缆向应用提供最高 240W 电力。
2. 支持新的高功率场景：USB 总线供电硬盘（HDD）、打印机等，省去独立电源砖。
3. 显示器从墙取电的同时可为笔记本供电/充电并正常显示画面。
4. USB 电源砖/充电器可穿透笔记本的 USB 口向后端设备供电。
5. 电池供电设备可从集线器获得更大充电电流，并在硬盘起转时临时回送电力。

## 5. 与 USB BC（Battery Charging）的共存

- USB PD 设计为**与标准 USB Battery Charging（BC）实现共存**（官方页原文："Power Delivery is designed to co-exist with standard USB Battery Charging implementations"）。
- 若实现中包含电池充电能力或支持扩展主机适配器（docks、ACA），应同时参考 **USB Battery Charging Specification**（检索入口：usb.org documents，分类 Battery Charging）。

## 6. 合规测试与认证要求（官方页明列）

- **合规测试**：PD 合规测试信息见 usb.org 的 USB-C/PD 合规页面（https://www.usb.org/usbc）。
- **Integrators List**：除通过 USB-IF 合规测试并列入 Integrators List（https://cms.usb.org/integrators-list）外，使用认证 USB 标识还须持有有效的 **USB-IF 商标许可协议（Trademark License Agreement）**。
- **采用者协议（Adopters Agreement）**：USB PD 由相应采用者协议覆盖，按实现选择：
  - USB 2.0 产品：USB 2.0 采用者协议 + **"USB Power Delivery Specification" 采用者协议**
  - USB 3.2 产品：USB 3.0 采用者协议 + USB 2.0 采用者协议 + PD 采用者协议
  - USB4 产品：USB4 采用者协议

## 7. 对本项目（手机配件电商）的映射要点

- "PD3.1 240W" 充电器的 240W 属于 **48V/5A EPR 档位**（28V→140W、36V→180W、48V→240W），**须同时满足**：充电器支持 EPR + 全功能 5A 线缆（E-Marker）+ 受电设备支持 EPR；三者缺一不可，**不可把 240W 线或 240W 充电器单方标称当作整套组合的实际功率**。
- 28V/36V/48V 是 PD3.1 新增固定档；15V~最高固定电压之间为 AVS 可调区间。
- 支持 PD 的设备仍需兼容 BC（BC 1.2 等旧协议共存），商品页协议清单中 "BC1.2" 与 "PD" 并列属正常。
- 认证表述：宣称"USB-IF 认证"应可对应到 Integrators List；仅写"符合 PD3.1"而无认证标识的，按未认证处理。

## 8. 未尽事项（pending）

- EPR/SR（Standard Power Range）的规范级定义、PPS（Programmable Power Supply，属 PD3.0 SPR 可调档）与 AVS 的关系细节：usb.org 本页未展开，需另行查阅 PD 规范原文后补充。
- UFCS 为中国信通院主导的融合快充标准，非 USB-IF 范畴，仅作对照记录。

---
**来源**
1. [USB Charger (USB Power Delivery) - USB-IF 官方专题页](https://www.usb.org/usb-charger-pd) — 采集时间 2026-10-07
2. [USB PD 3.1 发布公告 PDF（2021-05）- usb.org](https://www.usb.org/sites/default/files/2021-05/USB%20PG%20USB%20PD%203.1%20DevUpdate%20Announcement_FINAL.pdf) — 采集时间 2026-10-07
3. [USB-IF 合规测试页](https://www.usb.org/usbc) / [Integrators List](https://cms.usb.org/integrators-list) — 采集时间 2026-10-07

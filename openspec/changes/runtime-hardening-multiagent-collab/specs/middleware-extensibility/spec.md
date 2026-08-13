## Purpose

定义 AgentCore 中间件扩展性的行为契约，提供跨 Agent 的公开中间件注入机制，并以 SDK 公开 API 替代私有中间件依赖，支撑 PII 脱敏、审计与追踪等横切能力。

## ADDED Requirements

### Requirement: 全局中间件扩展入口
系统 MUST 提供跨 Agent 的全局中间件注入机制，使运营人员无需改动 AgentFactory 代码即可为所有或指定 Agent 插入自定义中间件。

#### Scenario: 全局中间件注入
- **WHEN** 运营人员在全局配置中注册一个自定义中间件
- **THEN** 后续构建的所有 Agent SHALL 在 SDK 规定的插入点包含该中间件。

#### Scenario: 按 Agent 选择性注入
- **WHEN** 运营人员指定某个自定义中间件仅应用于特定 Agent
- **THEN** 未被选中的 Agent SHALL 不加载该中间件。

#### Scenario: 中间件叠加
- **WHEN** 多个全局中间件被注册
- **THEN** 它们 SHALL 按声明顺序叠加应用，且不覆盖 SDK 内置中间件。

### Requirement: 公开中间件 API 使用
系统 MUST 通过 SDK 公开的中间件 API 实现工具治理与自定义能力，不得依赖 SDK 私有内部实现。

#### Scenario: 工具排除通过公开 API
- **WHEN** 系统需要禁用某个内置工具
- **THEN** 该禁用 SHALL 通过 SDK 公开的中间件或工具面配置实现，而非私有内部类。

#### Scenario: SDK 升级兼容
- **WHEN** SDK 升级且其内部实现变化
- **THEN** 系统 SHALL 在无源码修改的情况下继续使用公开中间件 API，不产生静默失效。

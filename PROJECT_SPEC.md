# 供应商风险分析 Agent 项目说明书

## 项目背景

本项目由企业 AI 小组开发。

目标是构建一个面向采购业务的供应商风险分析 Agent。

采购人员输入供应商名称后，系统自动：

1. 查询企业工商信息
2. 查询企业风险信息
3. 查询上市公司财报数据
4. 计算供应商风险等级
5. 生成采购风险分析报告
6. 给出合作建议

项目定位：

企业级 AI Agent MVP。

---

# 项目目标

实现如下流程：

用户输入企业名称

↓

查询企业信息

↓

查询风险信息

↓

判断是否上市公司

↓

查询财报

↓

风险评分

↓

生成风险报告

↓

输出采购建议

---

# 技术架构

## Agent层

Dify Workflow

负责：

* 流程编排
* HTTP调用
* LLM分析
* 报告生成

不负责：

* 风险评分计算
* 数据清洗
* 财报分析计算

以上逻辑必须放在后端实现。

---

## 后端

技术栈：

* Python 3.11+
* FastAPI
* Pydantic
* SQLAlchemy

职责：

* 封装天眼查API
* 查询财报数据
* 风险评分计算
* 提供HTTP接口

---

## 数据来源

### 天眼查

获取：

* 企业基本信息
* 法人
* 注册资本
* 成立时间
* 风险信息
* 被执行人
* 诉讼信息
* 经营异常

### 财报数据库

获取：

* 营业收入
* 净利润
* ROE
* 毛利率
* 经营现金流
* 资产负债率

---

# 系统模块

## 模块1：企业信息查询

接口：

GET /company/profile

输入：

company_name

输出：

{
"company_name": "",
"legal_person": "",
"registered_capital": "",
"establish_time": "",
"is_listed": true
}

---

## 模块2：风险信息查询

接口：

GET /company/risk

输入：

company_name

输出：

{
"lawsuit_count": 0,
"executed_count": 0,
"abnormal_operation_count": 0,
"administrative_penalty_count": 0
}

---

## 模块3：财报查询

接口：

GET /financial/metrics

输入：

company_name

输出：

{
"revenue_growth": 0.12,
"net_profit_growth": 0.08,
"debt_ratio": 0.65,
"cash_flow": 100000000
}

---

## 模块4：风险评分

接口：

POST /risk/calculate

输入：

企业信息

风险信息

财务指标

输出：

{
"risk_score": 58,
"risk_level": "中风险"
}

---

# 风险评分规则

总分范围：

0 ~ 100

风险等级：

0-30：

低风险

31-60：

中风险

61-100：

高风险

---

## 财务风险

资产负债率 > 70%

+20

经营现金流 < 0

+20

净利润连续下降

+15

营收连续下降

+15

---

## 司法风险

存在被执行记录

+25

存在失信记录

+30

存在重大诉讼

+20

---

## 经营风险

经营异常

+10

行政处罚

+10

频繁法人变更

+10

---

# Dify Workflow设计

节点：

Start

↓

HTTP：企业信息

↓

HTTP：风险信息

↓

Condition：

是否上市公司

↓

是

↓

HTTP：财报信息

↓

HTTP：风险评分

↓

LLM分析

↓

End

---

# LLM Prompt要求

角色：

采购风险分析专家

要求：

1. 不允许编造数据
2. 必须依据接口返回数据分析
3. 输出采购视角建议
4. 输出Markdown格式
5. 风险结论必须放在最前面

---

# 输出模板

# 供应商风险分析报告

## 风险结论

风险等级：

风险评分：

## 企业基本信息

企业名称：

法人：

成立时间：

注册资本：

## 风险分析

### 财务风险

### 司法风险

### 经营风险

## 综合评价

## 采购建议

---

# 项目目录结构

backend/

├── app/

│ ├── api/

│ ├── services/

│ ├── models/

│ ├── schemas/

│ ├── utils/

│ └── main.py

├── tests/

└── requirements.txt

---

# 开发原则

1. 优先实现MVP
2. 优先保证流程跑通
3. 不做复杂多Agent
4. 风险评分必须代码计算
5. LLM只负责解释与总结
6. 所有接口必须提供Mock数据
7. 所有模块必须编写单元测试

---

# 第一阶段目标

完成以下功能：

[ ] 企业信息查询

[ ] 风险信息查询

[ ] 财报查询

[ ] 风险评分

[ ] Dify Workflow

[ ] 风险报告生成

完成后可演示：

输入企业名称

↓

自动生成供应商风险分析报告


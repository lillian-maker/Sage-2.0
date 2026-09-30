"""Reviewed English homepage copy. Run through build.py, not in the browser.

Terms: digital employees, business workflows, human authorization, credits.
Proper names retain their romanized identity; translation adds no capability claims.
"""
import re
import json
from pathlib import Path

COPY = r"""
Sage · 你的出海 AI 团队	Sage · Your AI Team for Global Commerce
Sage，53个数字员工组建完整的经营组织。结论有证据，执行经人授权。	Sage brings 53 digital employees into one business organization. Evidence-backed decisions. Human-authorized execution.
洞察 · 决策 · 执行	Insight · Decisions · Execution
你的<span class="hero-team-emphasis">出海 AI 团队</span>	Your <span class="hero-team-emphasis">AI Team for Global Commerce</span>
53个数字员工组建完整的经营组织。结论有证据，执行经人授权。	53 digital employees. One complete business organization. Evidence-backed decisions. Human-authorized execution.
跳转到正文	Skip to content
主导航	Main navigation
打开导航菜单	Open navigation menu
下载Sage	Download Sage
免费体验	Try Sage
咨询和演示	Talk to us
AI 团队	AI Team
企业服务	Enterprise
价格	Pricing
文档	Docs
登录	Log in
产品</span>	Product</span>
九个经营场景的协作演示	Team collaboration across nine business workflows
切换经营场景	Next business workflow
持续转动的地球，点亮全球经营目标	Rotating globe showing global business roles
持续转动的地球，展示全球经营目标	Rotating globe showing global business roles
看见Sage如何工作	See Sage at work
Sage 产品录屏占位	Sage product recording placeholder
AI 团队协作录屏占位	AI team collaboration recording placeholder
各有专长，一起把事做好	Specialists working as one
依据可追溯，行动有边界	Traceable evidence. Controlled action.
分身资产	Reusable expertise
专业可积累	Expertise that grows
各司其职	Clear responsibilities
成果相衔接	Connected deliverables
Agent 治理	Agent governance
能力有边界	Defined boundaries
组织</span>	Organization</span>
协作</span>	Collaboration</span>
选择可信机制	Choose a trust mechanism
结论有出处	Evidence-backed decisions
执行先授权	Authorization before action
结果可核验	Verifiable outcomes
独立复核	Independent review
人的决策权	Human decision rights
版本与审计	Versioning and audit
在你的企业中，<br>真正落地。	Built for your business.<br>Deployed with purpose.
从既有产品启用，到深度接入业务。<br>选择与你当前阶段相符的路径。	Start with the product or integrate it into your operations.<br>Choose the path that fits your business.
产品采购	Product procurement
FDE 落地	FDE deployment
场景选择 → 组织与数据 → 权限 → 验收	Workflow → Organization and data → Permissions → Acceptance
问题界定 → 受控试点 → 联合验收 → 持续运行	Scope → Controlled pilot → Joint acceptance → Ongoing operation
下一件经营工作，<br>一起开始。	Your next business challenge.<br>Start with Sage.
聊聊你的业务	Tell us about your business
公司 / 品牌	Company / brand
访客手机号	Phone number
工作邮箱（选填）	Work email (optional)
想解决的经营问题	What business problem would you like to solve?
联系方式仅用于业务咨询。当前预览未接通接收服务，填写内容不会发送或保存。	Contact details are for business inquiries only. This preview has no connected submission service. Nothing you enter is sent or saved.
咨询接收服务尚未接通。内容未发送、未保存，请勿在预览中填写敏感资料。	The inquiry service is not connected. Your information was not sent or saved. Please do not enter sensitive information in this preview.
AI 团队，立足真实业务。	An AI team grounded in real business.
本地产品预览 · 认证、支付与真实任务尚未接入	Product preview · Authentication, payments and live tasks are not connected
场景资料未加载	Workflow data failed to load
牵头	Lead
保障	Assurance
协作'	Contributor'
交接给：	Hand off to: 
汇总交付：	Final deliverable: 
'成果'	' deliverable'
每个判断，都能回到依据。	Every decision leads back to its evidence.
保留来源、对象与分析范围。将支持、反证和待验证假设分开，而不是只交付一个答案。	Preserve sources, subjects and analytical scope. Separate supporting evidence, counterevidence and untested assumptions—not just an answer.
研究产物	Research deliverable
消费者反馈 · 来源	Customer feedback · Sources
多维标签 · 分析范围	Multidimensional tags · Analytical scope
支持与反证 · 判断依据	Supporting and opposing evidence · Decision basis
可追溯的结论	Traceable conclusions
产物版本与接受标准	Deliverable versions and acceptance criteria
证据不足时，先补证	Insufficient evidence? Gather more first.
能提出建议，不等于能执行。	A recommendation is not permission to act.
明确组织、店铺与操作对象。权限和积分预算在模型之外校验，需要人确认的动作不会被自动略过。	Define the organization, store and target of each action. Permissions and credit budgets are checked outside the model. Required human approvals cannot be skipped.
待执行方案	Proposed action plan
店铺与商品 · 操作对象	Store and products · Action targets
读取或写入 · 权限范围	Read or write · Permission scope
预计与最高消耗 · 积分预算	Estimated and maximum use · Credit budget
权限校验	Permission check
有边界的行动	Bounded execution
按授权范围执行	Act only within the authorized scope
未获授权，不执行	No authorization. No execution.
有回执，才有完成的依据。	Completion needs a verified execution record.
对照实际回执判断完成、失败或未知。保留已有成果与错误位置，不把“已发起”当作“已完成”。	Use actual execution records to distinguish completed, failed and unknown states. Preserve existing work and error details. Initiated does not mean completed.
任务与产物	Tasks and deliverables
已接受产物 · 版本	Accepted deliverables · Versions
执行请求 · 具体对象	Execution requests · Specific targets
实际回执 · 对照核验	Execution records · Verification
回执核验	Execution verification
可核验的结果	Verifiable results
完成 / 失败 / 未知	Completed / Failed / Unknown
状态未知，先核查	Unknown status? Verify before proceeding.
产品机制示意	Product design illustration
存量GMV增长	Grow existing revenue
新品证据到商业验证	Validate new products
供需补货与交付	Replenishment and fulfillment
新市场与合规上架	Market entry and compliance
客户旅程与复购	Customer journey and retention
需求到数据产品与业务工具	Data products and business tools
质量与账号重大事件	Quality and account incidents
经营复盘与能力组合更新	Business review and capability renewal
Shopify运营	Shopify operations
经营行动包	Business action plan
新品命题与验证方案	Product hypothesis and validation plan
约束补货与履约计划	Constraint-based replenishment and fulfillment plan
市场进入与发布包	Market entry and launch package
客群修复与复购方案	Customer recovery and retention plan
可验收的数据/工具产品	Acceptance-ready data products and tools
事件处置与恢复证据	Incident response and recovery evidence
经营与能力组合决策	Business and capability portfolio decisions
店铺诊断与优先行动清单	Store diagnosis and prioritized actions
机会证据包	Opportunity evidence package
建单与口径	Scope and definitions
渠道与增长诊断	Channel and growth diagnosis
供需可行性	Supply and demand feasibility
行动包装配	Action plan assembly
结果复核	Outcome review
机会证据	Opportunity evidence
产品定义	Product definition
质量与供应验证	Quality and supply validation
上市实验	Launch experiments
投资组合裁决	Portfolio decision
需求基线	Demand baseline
供应与资金约束	Supply and cash constraints
计划求解	Plan development
履约执行	Fulfillment execution
经营核对	Operational reconciliation
机会与范围	Opportunity and scope
产品和内容准备	Product and content readiness
渠道发布设计	Channel launch planning
上线与核对	Launch and verification
首周期复盘	First-cycle review
事件汇聚	Event consolidation
问题诊断	Problem diagnosis
跨域修复	Cross-functional resolution
触达执行	Customer outreach
业务定义	Business requirements
数据设计	Data design
工具实现	Tool development
门禁发布	Gated release
价值复盘	Value review
信号验证	Signal verification
最小保护	Immediate safeguards
事件处置	Incident response
根因复核	Root-cause review
恢复裁决	Recovery decision
经营事实	Business facts
风险与财务复核	Risk and financial review
能力诊断	Capability assessment
变更发布	Change release
周期裁决	Cycle-end decision
范围确认	Scope confirmation
经营快照	Business snapshot
多维诊断	Multidimensional diagnosis
行动与实验	Actions and experiments
回执与复核	Execution verification and review
唯一对象、口径和快照	One defined target, shared definitions and a dated snapshot
按实际渠道选贡献岗位；排序和反证	Select specialists for the actual channels; prioritize findings and test counterevidence
库存、交付、资金约束进入方案	Account for inventory, fulfillment and cash constraints
逐对象意图、预算、停止条件	Specify each target, action, budget and stop condition
回执齐备，能区分结果与归因	Complete execution records; separate observed outcomes from attribution
可追溯、去重，反证不被隐藏	Traceable, deduplicated evidence—with counterevidence kept visible
可证伪命题和验证设计	Falsifiable hypotheses and a validation design
外部实测/制造/准入证据，不让AI代造	Use real testing, manufacturing and compliance evidence—never AI-fabricated substitutes
有边界的实验与渠道执行回执	Bounded experiments with channel execution records
继续/转向/停止理由与证据一致	Evidence supports the decision to continue, pivot or stop
需求版本和误差明确	Document the forecast version and error range
候选供应及约束有凭据	Evidence supports supply options and constraints
逐SKU/节点计划可执行	Actionable plans for each SKU and fulfillment node
各环节实际回执与异常	Actual execution records and exceptions at every stage
可售、结算、资金口径一致	Align sellable inventory, settlement and cash definitions
唯一市场/渠道/账号/产品	A clearly identified market, channel, account and product
主数据、本地化与准入证据	Master data, localized content and market-entry evidence
逐对象意图、权限与失败处置	Define target-level actions, permissions and failure handling
平台、库存、结算支持实际可售	Verify platform readiness, inventory and settlement before selling
证据支持扩大/修复/退出	Evidence supports expansion, remediation or exit
去重与分群事件可追溯	Deduplicated, segmented events with traceable sources
根因与影响区分事实/假设	Distinguish facts from hypotheses about causes and impact
修复包不超隐私与产品边界	Keep remediation within privacy and product boundaries
逐客户/客群授权与回执	Authorization and execution records for each customer or segment
关闭/复购/质量关联有证据	Evidence links case resolution, repeat purchase and quality
用户/决策/对象/验收明确	Define the user, decision, target and acceptance criteria
口径血缘与失败语义明确	Document definitions, lineage and failure states
最小候选与可验证接口	A minimal candidate with verifiable interfaces
读写边界、验收及版本回退	Read/write boundaries, acceptance checks and version rollback
采用、SLO与业务价值分开	Measure adoption, service objectives and business value separately
权威信号或明确的建单前异常	An authoritative signal or a documented pre-case exception
固定动作意图与可靠投递边界	Fixed action intent with defined delivery guarantees
处置回执、风险与未决项	Response records, risks and unresolved issues
证据化根因与整改，不事后编故事	Evidence-based root causes and corrective actions—not retrospective narratives
正常策略与恢复证据成立	Confirm the operating policy and evidence of recovery
事实与异常可比、可追溯	Comparable, traceable facts and exceptions
差距经过独立复核	Independently review performance gaps
岗位/技能/工具/运行缺口明确	Identify gaps in roles, skills, tools and operations
不可变版本与回退依据	Immutable versions with a documented rollback basis
继续/停止/纠偏/再验证	Continue, stop, correct course or revalidate
具体店铺、页面与只读范围得到确认	Confirm the store, pages and read-only scope
口径与抓取时间明确；未知不填零	Record definitions and capture time; never treat unknown values as zero
每个问题指向页面或授权数据证据	Link every finding to a page or authorized data source
优先行动有依据；写入需另行授权	Evidence-based priorities; write access requires separate authorization
按主价值流核对回执和结果，不将草稿当发布	Verify execution records against the core workflow; a draft is not a publication
经营目标与资源统筹	Business goals and resource planning
场景自主编排与异常协调	Workflow orchestration and exception handling
经营分析与决策支持	Business analysis and decision support
组织能力与人事行政	Organizational capabilities and people operations
内控审计与独立复核	Internal controls and independent review
消费者需求与 VOC 研究	Customer needs and voice-of-customer research
市场竞争与机会研究	Market, competitor and opportunity research
产品组合与新品孵化	Product portfolio and new-product incubation
产品定义与商业立项	Product definition and business cases
工业设计与用户体验	Industrial design and user experience
硬件结构与材料工程	Hardware, mechanical and materials engineering
软件算法与应用生态	Software, algorithms and application ecosystems
产品验证与研发项目	Product validation and R&D projects
OEM 供应商开发与协同	OEM sourcing and supplier collaboration
采购与合同履约	Procurement and contract fulfillment
需求预测与补货计划	Demand forecasting and replenishment planning
库存与商品生命周期	Inventory and product lifecycle management
生产协同与质量控制	Production coordination and quality control
跨境物流与关务	Cross-border logistics and customs
仓储履约与退货处置	Warehousing, fulfillment and returns
Amazon 业务经营	Amazon business operations
Amazon 商品与搜索运营	Amazon listings and search optimization
独立站经营与转化	DTC store operations and conversion
其他平台与新市场经营	New platforms and market expansion
零售渠道与 B2B 拓展	Retail channels and B2B development
定价促销与商品组合	Pricing, promotions and assortment
店铺账号健康与规则	Store account health and platform rules
本地化与市场适配	Localization and market adaptation
品牌战略与传播	Brand strategy and communications
内容与创意策划	Content and creative strategy
视觉视频与素材生产	Visual, video and creative production
效果广告投放	Performance advertising
达人与联盟合作	Creator and affiliate partnerships
CRM 留存与复购	CRM, retention and repeat purchase
增长实验与增量评估	Growth experiments and incrementality
售前服务与购买指导	Presales support and purchase guidance
售后客诉与服务补救	Customer complaints and service recovery
体验洞察与质量反馈	Experience insights and quality feedback
用户教育与会员社区	Customer education and member communities
GMV 结算与会计对账	Sales settlement and accounting reconciliation
经营财务与资金	Business finance and cash management
税务与跨境实体协作	Tax and cross-border entity coordination
法务与知识产权	Legal affairs and intellectual property
产品合规与隐私	Product compliance and privacy
业务口径与主数据	Business definitions and master data
数据工程与质量	Data engineering and quality
系统集成与业务工具	Systems integration and business tools
知识技能与 Playbook 治理	Knowledge, skills and playbook governance
Agent 平台与可靠运行	Agent platform and operational reliability
信息安全与权限	Information security and access control
衡远	Hengyuan
枢衡	Shuheng
明镜	Mingjing
知人	Zhiren
守衡	Shouheng
听澜	Tinglan
望野	Wangye
拓新	Tuoxin
定形	Dingxing
映物	Yingwu
砺器	Liqi
灵枢	Lingshu
求证	Qiuzheng
择源	Zeyuan
契约	Qiyue
知量	Zhiliang
衡仓	Hengcang
质守	Zhishou
通途	Tongtu
行舟	Xingzhou
北辰	Beichen
觅位	Miwei
自航	Zihang
拓域	Tuoyu
联商	Lianshang
衡价	Hengjia
守店	Shoudian
译境	Yijing
立言	Liyan
叙事	Xushi
绘影	Huiying
点火	Dianhuo
结伴	Jieban
续缘	Xuyuan
试真	Shizhen
安心	Anxin
解忧	Jieyou
回声	Huisheng
同行	Tongxing
清账	Qingzhang
守金	Shoujin
合账	Hezhang
律衡	Lvheng
安界	Anjie
同尺	Tongchi
清源	Qingyuan
接桥	Jieqiao
积知	Jizhi
稳行	Wenxing
门卫	Menwei
先找增长的突破口。	Find the next growth opportunity.
把增长约束，变成行动。	Turn constraints into an action plan.
从真实需求出发。	Start with real customer needs.
让新品想法，有据可依。	Build new products on evidence.
补多少，何时到？	How much stock, and when?
让补货计划接上交付。	Connect replenishment to delivery.
先看清市场的门槛。	Understand the market requirements.
准备齐了，再迈出去。	Get ready before entering.
沿着客户的脚步看。	Follow the customer journey.
找到体验中断的地方。	Find where the experience breaks.
先把业务问题说清。	Define the business problem first.
让数据成为趁手的工具。	Make data useful in daily work.
先控制影响范围。	Contain the impact first.
恢复之后，还要复核。	Verify recovery before moving on.
一起回看这一轮。	Review the cycle together.
把经验带进下一轮。	Carry the learning forward.
从这家店的现状开始。	Start with the store as it is.
先诊断，再决定怎么改。	Diagnose first. Then decide.
从经营判断到业务执行，<br>让合适的数字员工承担明确的责任。	From business decisions to execution,<br>give the right digital employees clear responsibilities.
认识 50 位数字员工	Meet 50 digital employees
四个协作平面	Four collaboration layers
团队章节	Team sections
全员名录	Employee directory
团队协作	Team collaboration
认识你的 AI 团队	Meet your AI team
搜索员工、职责或交付物	Search employees, responsibilities or deliverables
搜索姓名、职责或交付物	Search names, roles or deliverables
按专业域筛选	Filter by professional domain
管理决策层：经营方向、增长与治理	Management: direction, growth and governance
CEO / CGO / CGOv 是管理决策职能，不计入 50 个执行岗位。	CEO / CGO / CGOv are management decision-making functions, separate from the 50 execution roles.
独立复核结论直达 CEO 与真人所有者；涉及治理官自身的争议，由真人所有者裁决。	Independent reviews report directly to the CEO and human owner. The human owner resolves disputes involving the governance officer.
专长背后，是可维护的资产	Expertise built on maintainable assets
身份、方法和运行配置各自有据，不靠一句提示词定义岗位。	Identity, methods and runtime settings are documented separately. A role is more than a prompt.
知道自己负责什么	Know what to own
岗位职责 · 交付标准 · 责任边界	Responsibilities · Delivery standards · Accountability boundaries
知道工作如何完成	Know how to deliver
输入依据 · 工作方法 · 验收条件	Input evidence · Working methods · Acceptance criteria
知道能力如何运行	Know how to operate
知识与工具 · 版本 · 访问范围	Knowledge and tools · Versions · Access scope
查看资产契约与维护机制	View asset contracts and maintenance
五类契约	Five contract types
业务、技能、运行、访问与保障，分别说明责任、方法、约束、权限和验证。	Business, skill, runtime, access and assurance contracts define responsibilities, methods, constraints, permissions and verification.
四类能力供应	Four capability supply streams
岗位与场景、知识与技能、工具与连接、发布与运行持续维护。	Roles and workflows, knowledge and skills, tools and connections, releases and operations are continuously maintained.
具体岗位绑定与版本以发布契约为准；当前展示资产结构，不代表已完成生产发布。	Role bindings and versions are governed by release contracts. This illustrates the asset structure, not a completed production release.
让成果接力，而不只是对话	Hand off deliverables, not just messages
一位负责人，明确的参与者，每一步都有交接标准。	One accountable owner, defined contributors and clear handoff criteria at every step.
选择一项工作，查看五步协作	Choose a workflow to explore five collaboration stages
研究可以止于成果。涉及外部操作时，另行确认权限并核对真实回执。	Research may end with a deliverable. External actions require separate authorization and verified execution records.
能力可进化，边界不越过	Evolving capabilities. Consistent boundaries.
先验证，再开放；出现异常，保留证据并隔离影响。	Validate before release. When exceptions occur, preserve evidence and isolate the impact.
能力生命周期	Capability lifecycle
人保留决策权	People retain decision rights
按政策确认关键动作，AI 不能自行扩大授权。	Confirm critical actions under policy. AI cannot expand its own authorization.
权限在模型之外	Permissions enforced outside the model
访问对象、动作与资源范围，由运行边界约束。	Runtime controls constrain accessible objects, actions and resources.
回退有明确边界	Explicit rollback boundaries
版本回退不等于撤销业务动作，实际撤回需核对系统与回执。	Rolling back a version does not reverse business actions. Any reversal requires system checks and execution evidence.
岗位、协作与治理为产品设计范围；本页不是生产发布记录或审计报告。	Roles, collaboration and governance describe the product design scope. This page is not a production release record or audit report.
让专业协作进入你的业务	Bring specialist collaboration into your business
企业产品与 FDE 落地	Enterprise products and FDE deployment
目标与资源	Goals and resources
专业执行	Specialist execution
数据与运行	Data and operations
全部员工	All employees
 / 50 位数字员工	 / 50 digital employees
没有找到匹配的员工。试试姓名、职责或交付物。	No matching employees. Try a name, responsibility or deliverable.
清空筛选	Clear filters
从名录选择其他员工	Choose another employee from the directory
主要交付	Primary deliverable
参与场景	Workflows
按任务范围参与	Assigned by task scope
查看分身资产	View reusable expertise
了解团队协作	Explore team collaboration
五步协作	Five-stage collaboration
主责	Accountable lead
交接要求	Handoff criteria
结果负责	Outcome owner
独立站示例，按渠道指定	DTC example; assigned by channel
参与与保障	Contributors and assurance
复核'	Review'
草稿	Draft
已评估	Evaluated
影子验证	Shadow validation
有限运行	Limited operation
生产发布	Production release
异常隔离	Exception isolation
退出使用	Retired
明确职责、输入、交付与边界；尚不代表可运行。	Define responsibilities, inputs, deliverables and boundaries. This does not yet mean runnable.
岗位契约与预设蓝图	Role contracts and preset blueprints
用明确用例检查能力与风险，保留失败项。	Evaluate capabilities and risks against defined cases; retain failures.
评估记录、用例与版本	Evaluation records, test cases and versions
受限观察候选输出，不将其作为正式动作。	Observe candidate outputs within a limited scope; do not treat them as authorized actions.
观察范围与差异记录	Observation scope and discrepancy records
限定对象、动作与资源，按验证逐步扩大。	Limit targets, actions and resources; expand only as validation supports it.
停止条件、审批与回执	Stop conditions, approvals and execution records
仅发布契约允许的能力进入正式运行。	Only capabilities permitted by release contracts enter production.
固定版本、权限与保障证据	Pinned versions, permissions and assurance evidence
停止受影响能力，保留问题与恢复条件。	Suspend affected capabilities; preserve issues and recovery conditions.
隔离原因、影响范围与证据	Isolation reasons, impact scope and evidence
停止新任务调用，同时保留可追溯历史。	Stop new task invocations while preserving traceable history.
替代方案与迁移边界	Alternatives and migration boundaries
依据：	Evidence: 
经营管理	Business management
业务运营	Business operations
独立控制	Independent controls
数据与Agent平台	Data and agent platform
经营与组织	Business and organization
产品与创新	Product and innovation
供应与履约	Supply and fulfillment
渠道经营	Channel operations
品牌与增长	Brand and growth
服务与体验	Service and experience
财务与合规	Finance and compliance
数据与 AI 运行	Data and AI operations
目标与资源决策包	Goals and resource allocation decisions
装配依据与自治异常分析	Orchestration rationale and autonomous-workflow exceptions
带来源的经营分析包	Source-backed business analysis
能力矩阵与组织调整建议	Capability matrix and organizational recommendations
复核与整改清单	Review and remediation checklist
需求证据与问题地图	Customer-needs evidence and issue map
机会证据与反证	Opportunity evidence and counterevidence
新品组合及继续停止建议	New-product portfolio and continue-or-stop recommendations
产品定义与立项包	Product definition and business case
设计与体验验证计划	Design and experience validation plan
技术风险与工程验证清单	Technical risks and engineering validation checklist
软件方案与验证证据	Software proposal and validation evidence
验证报告与缺陷闭合记录	Validation report and defect-resolution records
供应商能力与风险档案	Supplier capabilities and risk profile
采购建议与交期异常	Procurement recommendations and delivery exceptions
补货方案与预测区间	Replenishment plan and forecast intervals
库存风险与处置方案	Inventory risks and resolution plan
质量事件与纠正措施包	Quality incidents and corrective actions
运输方案与异常处置	Transport plan and exception handling
履约与退货处置记录	Fulfillment and returns records
Amazon 场景行动包	Amazon workflow action plan
商品与搜索优化包	Listing and search improvement plan
独立站经营与实验包	DTC operations and experiment plan
新渠道进入与验证方案	New-channel entry and validation plan
渠道机会与合作建议	Channel opportunities and partnership recommendations
价格与促销决策包	Pricing and promotion decisions
账号健康与处置建议	Account health and remediation recommendations
本地化内容与差异清单	Localized content and market differences
品牌原则与传播方案	Brand principles and communications plan
内容方案与证据引用	Content plan and evidence references
带版本的渠道素材包	Versioned channel creative assets
广告行动与效果报告	Advertising actions and performance report
合作候选与归因包	Partner candidates and attribution evidence
用户经营计划与实验结果	Customer engagement plan and experiment results
实验协议及继续停止结论	Experiment protocol and continue-or-stop decision
有依据的售前答复与线索	Evidence-backed presales responses and leads
客诉工单与补救建议	Complaint cases and recovery recommendations
体验问题与根因假设	Experience issues and root-cause hypotheses
教育内容与服务反馈	Educational content and service feedback
对账表与差异处理	Reconciliation statement and discrepancy resolution
现金与资源约束方案	Cash and resource constraints
税务与实体事项清单	Tax and legal-entity checklist
法律风险与处理建议	Legal risks and recommended responses
合规矩阵与证据缺口	Compliance matrix and evidence gaps
数据口径与对象关系契约	Data definitions and object relationship contracts
带质量状态的数据产物	Data deliverables with quality status
工具能力与验收包	Tool capabilities and acceptance package
能力版本与评估记录	Capability versions and evaluation records
运行状态与恢复证据	Runtime status and recovery evidence
权限矩阵与访问证据	Permission matrix and access evidence
从理解产品，到完成一项经营任务。	From understanding Sage to completing a business task.
文档目录	Documentation index
快速开始	Getting started
筹备中	In preparation
九个经营场景	Nine business workflows
AI 团队与协作	AI team and collaboration
VOC 机会洞察	VOC opportunity insights
Shopify 店铺诊断	Shopify store diagnosis
积分与账户	Credits and account
权限与治理	Permissions and governance
把每一步，写得清楚。	Clear guidance for every step.
产品使用手册与操作引导正在完善。当前目录展示文档规划，尚无可供检索的正式教程。	Product manuals and guides are being developed. This index shows the documentation plan; official searchable tutorials are not yet available.
先理解	Understand
产品范围与适用任务	Product scope and suitable tasks
再操作	Put it to work
输入、成果与下一步	Inputs, deliverables and next steps
有边界	Know the boundaries
权限、积分与保存规则	Permissions, credits and retention rules
官网中的方案和流程图是产品设计说明，不代表相应能力已接入或上线。	Website plans and diagrams describe the product design. They do not imply that the corresponding capabilities are connected or live.
返回产品	Back to product
下载 Sage。	Download Sage
下载 Sage	Download Sage
把经营工作放在手边。也可以直接从网页体验开始。	Keep your business work close at hand. Or start with the web preview.
桌面客户端	Desktop app
尚未发布	Not released
安装包和平台支持信息待提供。正式发布前，不提供有效下载入口。	Installers and platform support details are not yet available. Downloads will open after the official release.
当前版本	Current version
待发布	Pending release
支持平台 / 系统要求	Supported platforms / System requirements
待确认	To be confirmed
发布时间 / 文件校验	Release date / File verification
待提供	Not yet available
一个经营问题	One business question
一份可检查的成果	One reviewable deliverable
先在网页里体验。	Start with the web preview.
无需安装、无需先注册。从 VOC 机会洞察或 Shopify 店铺诊断的有限范围示例开始。	No installation or registration required. Explore a limited-scope VOC insight or Shopify diagnosis example.
当前为本地交互示例，真实分析服务尚未接入。	This is a local interactive example. Live analysis services are not connected.
正式发布时，一并提供。	Included with the official release.
更新说明	Release notes
版本变更与已知问题	Version changes and known issues
系统要求	System requirements
实际支持的平台与最低配置	Supported platforms and minimum specifications
安装指引	Installation guide
安装、升级与失败处理	Installation, upgrades and troubleshooting
文件校验	File verification
可核验的发布来源与完整性	Verifiable release source and file integrity
把 Sage 带进<br>你的经营流程。	Bring Sage into<br>your business operations.
启用既有产品，或围绕企业的真实问题，共同定义落地路径。	Start with the existing product, or shape a deployment around your business needs.
企业服务路径	Enterprise service paths
FDE 定制落地	Custom FDE deployment
从明确的场景开始。	Start with a defined workflow.
先确认产品、组织和数据边界，再以验收用例决定如何启用。	Confirm product, organizational and data boundaries, then use acceptance cases to guide deployment.
选择场景	Choose a workflow
确定经营问题	Define the business problem
组织与数据	Organization and data
明确使用范围	Define the scope of use
权限与积分	Permissions and credits
配置执行边界	Set execution boundaries
验收启用	Accept and launch
用例与证据核对	Verify cases and evidence
看得见的交付物。	Deliverables you can inspect.
以下为交付结构示意	Illustrative deliverable structure
开始之前，把责任分清。	Agree on responsibilities before starting.
Sage 提供	Sage provides
产品范围说明、接入方案与执行边界；按双方确认的范围交付。	Product scope, integration plans and execution boundaries. Delivery follows the mutually agreed scope.
客户准备	You provide
业务负责人、合法可用的数据、系统权限及必须核实的业务事实。	A business owner, lawfully usable data, system permissions and verified business facts.
共同确认	We agree together
目标、成本上限、权限矩阵、验收用例，以及运行中的问题接管方式。	Goals, cost limits, permission matrices, acceptance cases and operational escalation procedures.
从你的问题，<br>开始一场对话。	Start the conversation<br>with your business challenge.
无需先注册。描述当前经营问题，我们再讨论产品采购或专项落地的适合路径。	No registration required. Describe your challenge so we can discuss product procurement or a tailored deployment.
FDE 实施和持续服务单独界定，不包含在自助订阅承诺中。当前咨询接收服务尚未开放。	FDE implementation and ongoing services are scoped separately from self-service subscriptions. Inquiry submissions are not yet connected.
公司或品牌名称（选填）	Company or brand name (optional)
服务意向	Service interest
需要协助判断	Help me choose
联系手机号	Contact phone
必填	Required
电话区号	Calling code
其他区号	Other code
请填写含区号的示例号码	Enter an example number with calling code
工作邮箱	Work email
选填	Optional
希望解决的经营问题	Your business challenge
例如：希望先定位一个产品的体验问题，再形成可验证的改进任务。	For example: identify product experience issues and turn them into testable improvement tasks.
当前表单仅做本地检查，请勿填写真实联系方式或敏感业务信息。内容不会发送或保存。	This form only validates input locally. Do not enter real contact details or sensitive business information. Nothing is sent or saved.
咨询接收服务尚未开放，未发送或保存任何填写内容。当前仅完成本地格式检查；输入保留在本页，可继续修改。	The inquiry service is not connected. Nothing was sent or saved. Only local format validation was completed; you can continue editing this page.
围绕真实问题，共同落地。	Deploy together around a real problem.
从可检验的问题出发，用受控试点确认数据、系统和流程是否适合。	Start with a testable problem. Use a controlled pilot to validate the fit of data, systems and workflows.
问题界定	Problem definition
目标与验收条件	Goals and acceptance criteria
系统检查	System assessment
数据和授权现状	Current data and authorization
受控试点	Controlled pilot
有限范围验证	Limited-scope validation
交付验收	Delivery acceptance
产物与证据确认	Deliverable and evidence review
持续运行	Ongoing operation
责任与迭代机制	Responsibilities and iteration process
使用范围书	Statement of scope
经营目标	Business goal
一个明确的使用场景	One clearly defined workflow
交付对象	Recipients
负责人和使用团队	Accountable owner and user team
不含范围	Out of scope
另行确认的系统写入	System writes require separate agreement
权限矩阵	Permission matrix
组织、数据与任务	Organization, data and tasks
读取、生成、提交	Read, generate and submit
范围与接管规则	Scope and escalation rules
验收清单	Acceptance checklist
输入与预期产物	Inputs and expected deliverables
可复核的过程记录	Reviewable process records
负责方	Responsible parties
客户与 Sage 共同确认	Jointly agreed by the customer and Sage
试点范围书	Pilot scope
需要验证的经营判断	Business decision to validate
数据、系统与成本	Data, systems and costs
退出条件	Exit criteria
停止与恢复方式	Stop and recovery procedures
接入与权限矩阵	Integration and permission matrix
经授权的数据对象	Authorized data objects
允许的读取与执行	Permitted reads and actions
异常接管与审计	Exception escalation and audit
交付验收包	Delivery acceptance package
试点结果与已知限制	Pilot results and known limitations
复核步骤与证据	Review steps and evidence
交接与服务范围	Handoff and service scope
结构示意 · 非客户交付记录	Structure illustration · Not a customer delivery record
对象'	Target'
动作'	Action'
边界'	Boundary'
用例'	Test case'
证据'	Evidence'
问题'	Problem'
来源'	Source'
责任'	Responsibility'
产物'	Deliverable'
运行'	Operation'
关闭	Close
为下一阶段经营，<br>选择合适的方案。	Choose the right plan<br>for your next stage of growth.
从一次机会洞察，到团队协作，再到企业流程落地。	From a single insight to team collaboration and enterprise deployment.
方案类型	Plan type
个人 / 自助	Individual / Self-service
企业</button>	Business</button>
金额占位 · 正式权益待确认	Placeholder prices · Final plan details to be confirmed
请启用 JavaScript 查看候选套餐。当前没有开放支付，可前往企业服务页了解交付路径。	Enable JavaScript to view proposed plans. Payments are not available. Visit Enterprise to explore deployment paths.
讨论适合的方案	Discuss the right plan
关闭方案说明	Close plan details
收起	Collapse
0 / 20 / 60 / 200、40 / 20 均为沿用的版式占位数字，不是 Sage 有效报价。币种、计费周期、席位单位及积分规则确认后才会开放购买。	0 / 20 / 60 / 200 and 40 / 20 are layout placeholders, not valid Sage prices. Purchasing will open only after currency, billing periods, seat units and credit rules are confirmed.
任务需要更多计算，<br>按需补充积分。	Need more compute?<br>Add credits as required.
资源包只用于已授权任务的计算，不会扩大数据访问或店铺写入权限。	Credit packs fund compute for authorized tasks only. They do not expand data access or store write permissions.
查看资源包规则	View credit pack rules
积分使用说明	How credits work
剩余积分	Available credits
可用于新任务	Available for new tasks
预留积分	Reserved credits
确认任务后预留	Reserved after task confirmation
本次消耗	Credits used
按实际账本结算	Settled against the actual ledger
计费流程示意；当前未接入正式积分账本。	Illustrative billing flow. A production credit ledger is not connected.
资源包数量、单价、有效期、适用方案及退款规则尚未配置；当前不提供报价计算，不创建订单。注册或访问本页都不会追加积分。	Pack sizes, prices, validity, eligible plans and refund rules are not configured. No quotes or orders are created. Registration or visiting this page does not add credits.
把权益边界看清楚。	Understand what each plan covers.
以下为 Sage 权益讨论框架，具体分档与数量尚未确认。	This is a framework for discussing Sage plans. Tiers and quantities are not yet confirmed.
套餐权益对照，可横向滚动	Plan comparison; scroll horizontally
候选套餐权益对照，不构成正式服务承诺	Proposed plan comparison, not a service commitment
50 个 AI 岗位属于产品角色体系，不等于 50 个人类登录席位。购买方案不自动获得外部系统权限。	The 50 AI roles belong to the product role system; they are not 50 human login seats. A plan does not automatically grant external-system access.
你可能关心的问题。	Questions you may have.
积分如何消耗？	How are credits used?
计划流程是执行前展示预计及最高消耗，确认后预留积分，再按实际任务记录结算。预览示例不代表真实计价；积分数量、计算权重与有效期尚未确定。	The planned flow shows estimated and maximum usage before execution, reserves credits after confirmation, and settles against actual task records. Preview examples do not represent live pricing. Quantities, compute weights and validity are not yet defined.
失败或停止任务，如何结算？	How are failed or stopped tasks billed?
正式结算需要可验证的任务记录和积分账本。失败、部分完成、停止及退还规则仍待确认，当前不承诺统一全额扣减或全额退还。	Billing requires verifiable task records and a credit ledger. Rules for failures, partial completion, stopped tasks and refunds are not yet confirmed. No blanket full charge or full refund is promised.
注册后，成果能保留多久？	How long are deliverables retained after registration?
正式成果留存期限尚未确定。当前仅提供本地示例与当前浏览器会话状态，认证和长期保存尚未接入；注册不会追加免费积分。	Retention periods are not yet defined. The preview provides local examples and current browser-session state only. Authentication and long-term storage are not connected; registration does not add free credits.
团队订阅和 FDE 有什么不同？	How do team subscriptions and FDE differ?
团队方案讨论既有产品的组织协作与使用范围；FDE 围绕企业数据、系统和流程另行界定实施与持续服务。定制范围、费用与验收标准需独立确认。	Team plans cover collaboration and use of the existing product. FDE implementation and ongoing services are scoped separately around enterprise data, systems and workflows. Custom scope, fees and acceptance criteria require separate agreement.
选择方案后会自动继续原任务吗？	Will choosing a plan automatically resume my task?
不会。当前没有支付服务；返回原任务后，已有成果仍可查看。任何新增计算都需要重新确认范围与消耗。	No. Payments are not connected. Existing results remain viewable when you return to the task. Any new compute requires confirmation of scope and usage.
返回原任务	Return to the original task
查看方案不会追加积分或启动计算。	Viewing plans does not add credits or start compute.
返回原成果	Return to the original result
先判断一份成果是否有用。	First, see whether a deliverable is useful.
VOC 与 Shopify 有限范围示例	Limited-scope VOC and Shopify examples
查看完整示例成果	Review complete example deliverables
无需先注册	No registration required
围绕单个经营任务持续推进。	Keep a single business task moving forward.
候选：单任务分析与追问	Proposed: single-task analysis and follow-up
候选：成果保存与找回	Proposed: save and retrieve deliverables
候选：任务关联积分明细	Proposed: task-linked credit records
查看方案	View plan
把多个经营问题连成工作流。	Connect business questions into a workflow.
候选：多场景衔接	Proposed: connected workflows
候选：证据与成果版本	Proposed: evidence and deliverable versions
候选：更大的计算范围	Proposed: broader compute scope
让更复杂的经营工作有序展开。	Manage more complex business work.
候选：更高任务并发	Proposed: higher task concurrency
候选：持续经营分析	Proposed: ongoing business analysis
候选：更细的使用治理	Proposed: finer-grained usage governance
围绕共同目标，组织人类成员与 AI 岗位。	Bring people and AI roles together around shared goals.
候选：组织工作空间	Proposed: organizational workspaces
候选：成员权限与协作	Proposed: member permissions and collaboration
候选：共享计算预算与审计	Proposed: shared compute budgets and audit
讨论团队方案	Discuss a team plan
围绕企业数据、系统与流程，定义专项交付。	Define dedicated deliverables around enterprise data, systems and workflows.
候选：数据与系统接入评估	Proposed: data and systems integration assessment
候选：受控试点与交付验收	Proposed: controlled pilots and delivery acceptance
候选：持续服务范围	Proposed: ongoing service scope
讨论 FDE 落地	Discuss FDE deployment
体验'	Preview'
基础'	Basic'
专业'	Professional'
高级'	Advanced'
团队'	Team'
工作空间	Workspace
组织与项目边界待确认	Organization and project boundaries to be confirmed
人类席位	Human seats
登录人数、角色与协作范围待确认	User counts, roles and collaboration scope to be confirmed
积分'	Credits'
计算预算、共享与消耗规则待确认	Compute budgets, sharing and usage rules to be confirmed
产品范围	Product scope
场景、深度及实际开放能力待确认	Workflows, depth and available capabilities to be confirmed
数据连接	Data connections
连接数、接入类型与读写级别待确认	Connection counts, integration types and read/write levels to be confirmed
并发与任务限制	Concurrency and task limits
同时任务数与单任务上限待确认	Concurrent task counts and per-task limits to be confirmed
成果留存	Deliverable retention
保存期限、版本与导出范围待确认	Retention periods, versions and export scope to be confirmed
授权、证据与审计范围待确认	Authorization, evidence and audit scope to be confirmed
FDE 专项服务	Dedicated FDE services
实施、验收及持续服务独立确认	Implementation, acceptance and ongoing services agreed separately
金额待确认	Price to be confirmed
示例入口；正式体验积分待确认。	Example access; official trial credits to be confirmed.
权益讨论稿，不构成服务承诺。	Draft plan for discussion, not a service commitment.
权益维度	Plan dimension
VOC / Shopify 本地示例	Local VOC / Shopify examples
仅当前浏览器会话	Current browser session only
单独洽谈，不包含无限定制	Separately scoped; unlimited customization not included
权益讨论稿	Draft plan for discussion
当前未接通支付，没有创建订单或扣费。正式价格、积分和服务边界需要确认；你可以讨论需求，或返回原任务继续查看成果。	Payments are not connected. No order or charge was created. Final pricing, credits and service boundaries require confirmation. Discuss your needs or return to your task to review the results.
原任务	Original task
剩余积分与成果状态：返回任务查看。此页不改积分、不自动执行；任务页面会核对本会话记录。	Return to your task to check available credits and result status. This page does not change credits or execute tasks. The task page checks the current session record.
返回任务确认	Return to task confirmation
"""

def translate(text):
    pairs = dict(line.split('\t', 1) for line in COPY.strip().splitlines())
    extra = Path(__file__).with_name('english-experience.json')
    if extra.exists():
        pairs.update(json.loads(extra.read_text(encoding='utf-8')))
    pattern = re.compile('|'.join(re.escape(k) for k in sorted(pairs, key=len, reverse=True)))
    return pattern.sub(lambda match: pairs[match.group()], text).replace('。', '.').replace('，', ', ').replace('、', ', ').replace('：', ': ')

def english_links(text):
    return re.sub(r'(?<![\w-])(index|team|enterprise|pricing|docs|download|trial|auth|profile)\.html', r'\1-en.html', text)

def language_dropdown(html, page, english=False):
    label = 'Select language' if english else '选择语言'
    control = f'''<div class="sage-language"><button class="sage-language-toggle" type="button" aria-label="{label}" aria-expanded="false" aria-controls="sage-language-menu"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" aria-hidden="true"><circle cx="12" cy="12" r="9"/><ellipse cx="12" cy="12" rx="4" ry="9"/><path d="M3 12h18"/></svg></button><ul class="sage-language-menu" id="sage-language-menu" aria-label="{label}" hidden><li><a href="{page}-en.html" lang="en" hreflang="en" {'aria-current="true"' if english else ''}>English</a></li><li><a href="{page}.html" lang="zh-CN" hreflang="zh-CN" {'' if english else 'aria-current="true"'}>中文</a></li></ul></div>'''
    html, count = re.subn(r'<button\b[^>]*id="language-status-button"[^>]*>.*?</button>|<a\b[^>]*class="page-language"[^>]*>.*?</a>', lambda _: control, html, count=1)
    if count != 1:
        raise ValueError(f'Missing language control: {page}')
    fragment = Path(__file__).with_name('language-switch.html').read_text(encoding='utf-8')
    return html.replace('</body>', fragment + '</body>')

def english_homepage(html):
    html = translate(html)
    html = html.replace('lang="zh-CN"', 'lang="en"')
    html = english_links(html)
    # English copy expands naturally; preserve the approved composition without clipping.
    css = '''html[lang="en"] .v1 #hero-title{font-size:clamp(32px,3.1vw,52px);line-height:1.15;white-space:normal;max-width:100%;overflow-wrap:normal}
html[lang="en"] .v1 #hero-title .hero-team-emphasis{font-size:1.08em;white-space:normal;display:inline}
html[lang="en"] .hero-body{max-width:560px;line-height:1.65}
html[lang="en"] .reference-goal span{white-space:normal}
html[lang="en"] .relay-person strong{font-size:12px}
html[lang="en"] .relay-person>span{font-size:12px;line-height:1.45;overflow-wrap:anywhere}
html[lang="en"] .orbit-text b{font-size:12px}
html[lang="en"] .nav-links{gap:22px}
html[lang="en"] .nav-tools{gap:22px}
@media(max-width:760px){html[lang="en"] .v1 #hero-title{font-size:35px}html[lang="en"] .v1 #hero-title .hero-team-emphasis{font-size:1em}}
'''
    return html.replace('</style>', css + '</style>', 1)


def build_localized_pages(root):
    """Wrap legacy page fragments and emit reviewed English routes and scripts."""
    source, destination = root / 'src/pages', root / 'pages'
    titles = {'team':'AI 团队','enterprise':'企业服务','pricing':'价格','docs':'文档','download':'下载 Sage','product':'产品方案'}
    scripts = {'team':['catalog-data','team-page'], 'enterprise':['info-pages'], 'pricing':['info-pages'], 'product':['catalog-data','catalog']}
    for path in source.glob('*.js'):
        if path.stem == 'catalog':
            continue  # Unreleased product-design route; not linked from English navigation.
        code = english_links(translate(path.read_text(encoding='utf-8')))
        if path.stem == 'site-shell':
            code = re.sub(r"language\?\.addEventListener\('click',\(\)=>\{.*?\}\);", "language?.addEventListener('click',()=>{location.href=location.pathname.replace('-en.html','.html')+location.search+location.hash;});", code)
        (destination / (path.stem + '-en.js')).write_text(code, encoding='utf-8')
    for path in source.glob('*.html'):
        html = path.read_text(encoding='utf-8')
        if '<!doctype' not in html.lower():
            styles = ''.join(f'<link rel="stylesheet" href="{name}.css">' for name in ['redesign','pages','catalog','team-page','info-pages'])
            nav = '<nav class="r-links" aria-label="主导航"><span aria-disabled="true">产品</span><a href="team.html">AI 团队</a><a href="enterprise.html">企业服务</a><a href="pricing.html">价格</a><a href="docs.html">文档</a></nav>'
            header = f'<header class="r-nav"><a class="r-brand" href="../index.html">Sage</a>{nav}<div class="r-tools"><a class="page-language" href="{path.stem}-en.html">English</a><a href="auth.html">登录</a><a class="r-button small" href="../index.html#contact">咨询和演示</a></div></header>'
            footer = '<footer class="r-footer"><a href="../index.html">Sage</a><p>AI 团队，立足真实业务。</p><small>本地产品预览 · 认证、支付与真实任务尚未接入</small></footer>'
            js = ''.join(f'<script src="{name}.js" defer></script>' for name in scripts.get(path.stem,[]) + ['site-shell'])
            layout = '<style>.r-links{display:flex;align-items:center;gap:24px}.r-links a,.r-tools a{white-space:nowrap}.r-tools{display:flex;align-items:center;gap:24px}.r-footer{padding:48px 5%;display:flex;gap:24px;align-items:center;flex-wrap:wrap}html[lang="en"] .info-page-head h1{font-size:clamp(36px,4.5vw,64px)}html[lang="en"] .tp-person span{line-height:1.5}html[lang="en"] .tp-constellation h3{font-size:22px}@media(max-width:900px){.r-nav{flex-wrap:wrap;gap:18px}.r-links{order:3;width:100%;overflow:auto;gap:24px;padding-bottom:8px}.r-tools{margin-left:auto;gap:16px}.r-footer{display:block}.r-footer>*{margin-bottom:16px}}</style>'
            html = f'<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{titles.get(path.stem,"Sage")} · Sage</title>{styles}{layout}{js}</head><body>{header}{html}{footer}</body></html>'
        else:
            # Auth and trial already have their own shell; keep that layout intact.
            html = html.replace('</header>',f'<a class="page-language" href="{path.stem}-en.html">English</a></header>',1)
        (destination / path.name).write_text(language_dropdown(html, path.stem) if path.stem != 'product' else html,encoding='utf-8')
        if path.stem == 'product':
            (destination / 'product-en.html').unlink(missing_ok=True)
            (destination / 'catalog-en.js').unlink(missing_ok=True)
            continue
        en = english_links(translate(html)).replace('lang="zh-CN"','lang="en"')
        en = re.sub(r'src="([\w-]+)\.js"',r'src="\1-en.js"',en)
        en = en.replace(f'href="{path.stem}-en.html">English',f'href="{path.stem}.html" lang="zh-CN">中文')
        (destination / (path.stem + '-en.html')).write_text(language_dropdown(en, path.stem, True),encoding='utf-8')

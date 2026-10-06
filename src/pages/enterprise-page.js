(() => {
  'use strict';
  const page = document.querySelector('.sage-enterprise');
  if (!page) return;
  const phases = [
    ['问题诊断', '一起明确要支持的经营决策、当前做法与业务约束，确定目标、负责人及本次不包含的范围。', '问题说明、现状流程与基线指标', '业务负责人确认问题、目标与范围；资料不足时先补充，不直接开始实施。'],
    ['数据准备', '核对合法授权、字段口径、数据质量与现有系统的访问条件，识别关键缺口及撤权路径。', '数据就绪清单、字段映射与风险说明', '核心依据可获得，权限与口径有负责人确认；无法补齐时缩小范围或停止。'],
    ['联合设计', '用历史任务共同设计专业分工、成果格式、业务规则与人工确认点，先验证正常与边界情况。', '场景规格、样例成果与评测用例', '业务负责人认可成果格式；缺失、冲突与异常用例具有明确处理路径。'],
    ['受控试点', '在限定范围内进行只读或影子运行；必要的有限动作逐项批准，记录错误、接管、成本与结果差异。', '试点记录、问题清单与效果评估', '满足双方约定标准；关键风险消除或明确接受。试点通过不自动扩大授权。'],
    ['联合验收', '核验真实成果、权限、监测与异常升级，测试撤权、重复动作和失败处理，并交接运行责任。', '验收报告、运行手册与移交清单', '约定用例与接管路径通过核验，负责人签收；技术验收与经营效果分开确认。'],
    ['持续运行', '按约定范围复查效果、版本、数据变化与费用，把验证有效的方法沉淀为可维护的资产。', '版本记录、回归用例与维护说明', '新增范围先确认预算、责任与再验收；客户专有知识不跨企业复用。']
  ];
  const tabs = [...page.querySelectorAll('[data-phase]')];
  const panel = page.querySelector('#ep-phase-panel');
  function choosePhase(index, focus = false) {
    const phase = phases[index];
    tabs.forEach((tab, i) => {
      tab.setAttribute('aria-selected', String(i === index));
      tab.tabIndex = i === index ? 0 : -1;
    });
    panel.setAttribute('aria-labelledby', tabs[index].id);
    page.querySelector('.ep-phase-index').textContent = `${String(index + 1).padStart(2, '0')} / 06`;
    page.querySelector('#ep-phase-title').textContent = phase[0];
    page.querySelector('#ep-phase-description').textContent = phase[1];
    page.querySelector('#ep-phase-output').textContent = phase[2];
    page.querySelector('#ep-phase-gate').textContent = phase[3];
    panel.classList.remove('ep-phase-change');
    void panel.offsetWidth;
    panel.classList.add('ep-phase-change');
    if (focus) tabs[index].focus();
  }
  tabs.forEach((tab, index) => {
    tab.addEventListener('click', () => choosePhase(index));
    tab.addEventListener('keydown', event => {
      let next;
      if (event.key === 'ArrowRight') next = (index + 1) % tabs.length;
      if (event.key === 'ArrowLeft') next = (index + tabs.length - 1) % tabs.length;
      if (event.key === 'Home') next = 0;
      if (event.key === 'End') next = tabs.length - 1;
      if (next !== undefined) { event.preventDefault(); choosePhase(next, true); }
    });
  });
  const path = new URLSearchParams(location.search).get('path');
  if (!location.hash && (path === 'product' || path === 'fde')) {
    const target = page.querySelector(path === 'product' ? '#product-path' : '#fde-path');
    requestAnimationFrame(() => target.scrollIntoView({ behavior: 'instant', block: 'start' }));
  }
})();

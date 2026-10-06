(() => {
  'use strict';
  const page = document.querySelector('.sage-enterprise');
  if (!page) return;
  const phases = [
    ['Problem diagnosis', 'Define the decision to support, current practices and constraints together. Agree on objectives, ownership and exclusions.', 'Problem statement, current workflow and baseline metrics', 'The business owner confirms the problem, goals and scope. Gather missing information before implementation.'],
    ['Data preparation', 'Verify lawful authorization, field definitions, quality and system access. Identify critical gaps and revocation paths.', 'Data-readiness checklist, field mappings and risks', 'Core evidence is available and owners confirm permissions and definitions. Reduce scope or stop if gaps cannot be resolved.'],
    ['Joint design', 'Use historical tasks to jointly design roles, output formats, rules and human checkpoints. Validate normal and boundary cases first.', 'Scenario specifications, sample deliverables and evaluation cases', 'The owner accepts deliverable formats. Missing, conflicting and exceptional inputs have explicit handling paths.'],
    ['Controlled pilot', 'Run read-only or shadow work within a limited scope. Approve any necessary limited actions individually and record errors, takeovers, costs and outcome differences.', 'Pilot records, issues and outcome evaluation', 'Meet agreed criteria and eliminate or explicitly accept key risks. Pilot acceptance does not expand authorization automatically.'],
    ['Joint acceptance', 'Verify actual deliverables, permissions, monitoring and escalation. Test revocation, repeat actions and failure handling, then transfer operational responsibility.', 'Acceptance report, operating guide and handover checklist', 'Verify agreed cases and takeover paths with owner sign-off. Confirm technical acceptance separately from business impact.'],
    ['Ongoing operation', 'Review outcomes, versions, data changes and costs within the agreed scope. Preserve validated methods as maintainable assets.', 'Version records, regression cases and maintenance guidance', 'New scope needs budget, responsibility and renewed acceptance first. Customer-specific knowledge is not reused across companies.']
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

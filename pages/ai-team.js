(() => {
  'use strict';
  const catalog=window.SAGE_CATALOG;
  const directory=document.querySelector('#at-staff-directory');
  if(catalog?.people?.length&&catalog.domains?.length){
    catalog.domains.forEach((domain,index)=>{
      const group=document.createElement('section');
      group.className='at-staff-domain';
      const heading=document.createElement('h3');
      heading.id='at-staff-domain-'+index;heading.textContent=domain;
      group.setAttribute('aria-labelledby',heading.id);
      const grid=document.createElement('div');grid.className='at-staff-grid';
      grid.style.setProperty('--staff-count',catalog.people.filter(person=>person.domain===domain).length);
      catalog.people.filter(person=>person.domain===domain).forEach(person=>{
        const card=document.createElement('article');card.className='at-staff-card';
        const image=document.createElement('img');
        image.src=person.avatar;image.alt=person.name;image.width=64;image.height=64;
        image.loading='lazy';image.decoding='async';
        const name=document.createElement('h4');name.textContent=person.name;
        const role=document.createElement('p');role.className='at-staff-role';role.textContent=person.role;
        card.append(image,name,role);grid.append(card);
      });
      group.append(heading,grid);directory.append(group);
    });
  }else{
    directory.textContent='数字员工目录暂未加载，请刷新重试。';
    directory.setAttribute('role','status');
  }
  const icons=['person','file-earmark-text','database','diagram-3','people','search','shield-lock','bar-chart'];
  document.querySelectorAll('.at-principles article').forEach((article,index)=>{
    const image=document.createElement('img');
    image.src='icons/'+icons[index]+'.svg';image.alt='';image.width=28;image.height=28;
    article.prepend(image);
  });
})();

// App demo on the team page: plays only while visible, never under reduced motion, and can always be paused.
(function teamDemo(){
 const video=document.querySelector('#at-demo-video'),toggle=document.querySelector('#at-demo-toggle'),steps=[...document.querySelectorAll('#at-demo-steps li')],live=document.querySelector('#at-demo-live');
 if(!video||!toggle)return;
 const en=document.documentElement.lang==='en',motion=matchMedia('(prefers-reduced-motion: reduce)'),starts=steps.map(step=>Number(step.dataset.start));
 let userPaused=false,inView=false;
 const label=()=>{const playing=!video.paused;toggle.setAttribute('aria-pressed',String(playing));toggle.classList.toggle('is-playing',playing);toggle.querySelector('.at-demo-label').textContent=playing?(en?'Pause demo':'暂停演示'):(en?'Play demo':'播放演示');};
 const play=()=>{video.preload='auto';const attempt=video.play();if(attempt)attempt.catch(()=>{});};
 const chapter=()=>{const t=video.currentTime,end=video.duration||starts[starts.length-1]+8;let index=0;starts.forEach((start,i)=>{if(t>=start)index=i;});steps.forEach((step,i)=>{const from=starts[i],to=starts[i+1]??end;step.classList.toggle('is-active',i===index);step.style.setProperty('--p',i<index?1:i>index?0:Math.min(1,(t-from)/(to-from)).toFixed(3));});if(live)live.textContent=steps[index].querySelector('p').textContent;};
 video.addEventListener('play',label);video.addEventListener('pause',label);video.addEventListener('timeupdate',chapter);
 toggle.addEventListener('click',()=>{if(video.paused){userPaused=false;play();}else{userPaused=true;video.pause();}});
 steps.forEach((step,i)=>step.querySelector('.at-demo-seek').addEventListener('click',()=>{video.currentTime=starts[i];chapter();userPaused=false;play();}));
 new IntersectionObserver(entries=>entries.forEach(entry=>{inView=entry.isIntersecting;if(inView&&!userPaused&&!motion.matches)play();else if(!inView)video.pause();}),{threshold:.25}).observe(video);
 motion.addEventListener?.('change',()=>{if(motion.matches)video.pause();else if(inView&&!userPaused)play();});
 chapter();label();
})();

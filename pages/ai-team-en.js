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
    directory.textContent='The digital employee directory could not be loaded. Refresh to retry.';
    directory.setAttribute('role','status');
  }
  const icons=['person','file-earmark-text','database','diagram-3','people','search','shield-lock','bar-chart'];
  document.querySelectorAll('.at-principles article').forEach((article,index)=>{
    const image=document.createElement('img');
    image.src='icons/'+icons[index]+'.svg';image.alt='';image.width=28;image.height=28;
    article.prepend(image);
  });
})();

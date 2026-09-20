import {productRecords,productSources,productModes,productLocations} from './product-story';
import {studioViewIcons} from '../components/studio-view-detail';
import {semanticIconColors,type SemanticIconName} from '../components/semantic-icon';
export type AnatomyNode={id:string;name:string;icon:SemanticIconName;color:string;position:[number,number,number];image?:string;lane?:string;visual?:'window'|'files'|'runtime'};
const ring=(i:number,n:number,r=3.7):[number,number,number]=>{const a=Math.PI/2+i/n*Math.PI*2;return [Math.cos(a)*r,Math.sin(a)*r*.73,Math.sin(a*2)*.35]};
export const anatomyChapters=[
 {name:'Inside the project',heading:'Inside an Evidence Lane',accent:'project.',body:'One connected project. Separate records for its plan, conversations, sources and results. Open a glass orb to explore the responsibility of each.',side:'left'},
 {name:'Separate records',heading:'Every record has',accent:'a responsibility.',body:'The Plan owns the work. Memory retains context. Learning records useful procedures. Their references connect the project while their data remains separate.',side:'right'},
 {name:'Source collections',heading:'Your material.',accent:'Its own source trail.',body:'Code, documents, data and research enter through their own source paths. Choose a material to inspect what it contributes and the tools its operations declare.',side:'left'},
 {name:'Read, steer or execute',heading:'A question. A change.',accent:'A next step.',body:'Reading evidence, changing the Plan and carrying out work have different effects. Choose an intention to see what happens to the project.',side:'right'},
 {name:'Three locations',heading:'Source files. Project records.',accent:'Shared runtime.',body:'Your material, the project’s persistent records and the shared local engine have different homes. Their connections do not combine them into one store.',side:'left'},
 {name:'The Studio observer',heading:'See the work.',accent:'Follow its evidence.',body:'Studio presents the engine’s observations. Open a view to see the information it shows. Direct project changes through Codex.',side:'right'}
] as const;
export function anatomyNodes(chapter:number):AnatomyNode[]{
 if(chapter===2)return productSources.map(([id,name,image],i)=>({id:'material:'+id,name,icon:id==='local_code'||id==='github_code'?'work':id==='artifacts'?'results':'sources',color:['#f09c80','#a9c8fa','#8cbbff','#eea176','#85dfb2','#91d5ef','#bed7f1','#efd37b','#f3a3ba','#c8a1f0','#e5d089','#8fddbb','#baaaf3'][i],position:[i<6?-2.4:2.4,(i<6?2.5-i:3-(i-6))*1.2,0] as [number,number,number],image:image?'/brands/'+image:undefined,lane:image?undefined:id}));
 if(chapter===3)return productModes.map((m,i)=>({id:'mode:'+i,name:m.name,icon:(['sources','plan','work'] as const)[i],color:['#85def0','#bd98f4','#f0c384'][i],position:([[-3.5,.1,.1],[0,2.7,-.2],[3.5,.1,.1]] as [number,number,number][])[i]}));
 if(chapter===4)return productLocations.filter(l=>l.id!=='state').map((l,i)=>({id:'location:'+l.id,name:l.title,icon:l.icon,visual:i?'runtime':'files',color:i?'#efd088':'#88dcf0',position:[i?3.8:-3.8,.1,0]}));
 if(chapter===5)return Object.entries(studioViewIcons).map(([name,icon],i)=>({id:'studio:'+name,name,icon,color:semanticIconColors[icon],position:ring(i,9,3.9)}));
 const records:AnatomyNode[]=productRecords.map((r,i)=>({id:'record:'+r.id,name:r.name,icon:r.icon,color:r.color,position:chapter===1?[ring(i,8,3.25)[0],ring(i,8,3.25)[1]*1.38,Math.cos(i*Math.PI/4)*.8]:ring(i,8)}));
 return chapter===0?[...records,{id:'studio:Overview',name:'Studio',icon:'studio',color:'#8acfe5',position:[3.2,4.3,-.3],visual:'window'}]:records;
}

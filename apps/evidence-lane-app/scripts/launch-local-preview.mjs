import {spawn} from 'node:child_process';
import {existsSync,mkdirSync,openSync,closeSync} from 'node:fs';
import {createConnection} from 'node:net';
import {fileURLToPath} from 'node:url';
import path from 'node:path';

const root=fileURLToPath(new URL('../',import.meta.url));
const url='http://127.0.0.1:3100';
const checkOnly=process.argv.includes('--check');
const delay=ms=>new Promise(resolve=>setTimeout(resolve,ms));
const next=path.join(root,'node_modules','next','dist','bin','next');
const logs=path.join(root,'.preview');

async function portInUse(){return new Promise(resolve=>{const socket=createConnection({host:'127.0.0.1',port:3100});const finish=value=>{socket.destroy();resolve(value)};socket.once('connect',()=>finish(true));socket.once('error',()=>finish(false));socket.setTimeout(1500,()=>finish(false))})}
async function isPreview(){try{const response=await fetch(url,{signal:AbortSignal.timeout(3000)});if(!response.ok)return false;const html=await response.text();return html.includes('Evidence Lane')&&html.includes('evidence-lane-icon.png')}catch{return false}}
function chromePath(){return [path.join(process.env.PROGRAMFILES||'C:/Program Files','Google/Chrome/Application/chrome.exe'),path.join(process.env['PROGRAMFILES(X86)']||'C:/Program Files (x86)','Google/Chrome/Application/chrome.exe'),path.join(process.env.LOCALAPPDATA||'','Google/Chrome/Application/chrome.exe')].find(existsSync)}
async function launch(){
 if(!existsSync(next))throw new Error('The website dependencies are missing from this folder. Restore its node_modules before using this launcher. Nothing was installed or downloaded.');
 let ready=await isPreview();
 if(!ready&&await portInUse())throw new Error('Port 3100 is already in use, but an Evidence Lane preview did not respond. No existing process was stopped. Check the application using that port.');
 if(!ready){
  mkdirSync(logs,{recursive:true});const logPath=path.join(logs,'server.log');const fd=openSync(logPath,'a');
  console.log('Starting the local Evidence Lane website…');
  const child=spawn(process.execPath,[next,'dev','--hostname','127.0.0.1','--port','3100'],{cwd:root,detached:true,windowsHide:true,stdio:['ignore',fd,fd],env:{...process.env,NEXT_TELEMETRY_DISABLED:'1'}});closeSync(fd);
  await new Promise((resolve,reject)=>{child.once('spawn',resolve);child.once('error',reject)});child.unref();
  const deadline=Date.now()+90000;
  while(Date.now()<deadline){if(await isPreview()){ready=true;break}if(child.exitCode!==null)break;await delay(750)}
  if(!ready)throw new Error('The preview is not ready yet. Read '+logPath+' for the startup output.');
 }else console.log('Reusing the Evidence Lane preview already running on port 3100.');
 console.log('Local website: '+url);
 if(checkOnly)return;
 const chrome=chromePath();if(!chrome)throw new Error('Google Chrome was not found. The website is running; open '+url+' in your browser.');
 const browser=spawn(chrome,[url],{detached:true,stdio:'ignore',windowsHide:false});await new Promise((resolve,reject)=>{browser.once('spawn',resolve);browser.once('error',reject)});browser.unref();
 console.log('Chrome opened. The local server continues in the background.');
}
launch().catch(error=>{console.error('\n'+error.message);process.exitCode=1});

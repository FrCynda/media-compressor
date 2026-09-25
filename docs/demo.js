/* Browser-side stand-in for server.py: same /api/* contract, runs entirely in this tab.
   Folders: File System Access API (Chrome/Edge). Images: canvas. Video: ffmpeg.wasm (H.264 only). */
(()=>{
const VEXT=['.mp4','.mkv','.mov','.avi','.webm','.m4v','.wmv','.flv','.mpg','.mpeg','.ts'];
const H={inp:null,out:null};
const BLANK=()=>({status:'idle',kind:'images',total:0,done:0,partial:0,start:0,end:0,in_bytes:0,out_bytes:0,converted:0,verified:0,noparams:0,flattened:0,skipped:0,problems:0,overtarget:0,error:''});
let S=BLANK(), EST={status:'idle'}, cancel=false, CUR=0;
const ext=n=>{const i=n.lastIndexOf('.');return i<0?'':n.slice(i).toLowerCase()};
const want=(n,kind)=>{const e=ext(n);return (kind!=='videos'&&e==='.png')||(kind!=='images'&&VEXT.includes(e))};
const isVid=n=>VEXT.includes(ext(n));

async function walk(dir,kind,skipDir,rel=[],out=[]){
  if(dir.fb)return dir.files.filter(f=>want(f.name,kind)&&!f.name.toLowerCase().includes('.part.')).map(f=>({dir:null,rel:f.webkitRelativePath.split('/').slice(1,-1),name:f.name,h:{getFile:async()=>f}}));
  for await(const [name,h] of dir.entries()){
    if(h.kind==='directory'){ if(skipDir&&await h.isSameEntry(skipDir))continue; await walk(h,kind,skipDir,[...rel,name],out); }
    else if(want(name,kind)&&!name.toLowerCase().includes('.part.'))out.push({dir,rel,name,h});
  }
  return out;
}
const same=()=>H.inp.fb?{zip:true,entries:[],name:H.inp.name}:H.inp;
const outDir=()=>H.out&&H.inp&&H.out===H.inp?null:H.out;
async function dirAt(root,rel){if(root.zip)return {zip:root,rel};for(const n of rel)root=await root.getDirectoryHandle(n,{create:true});return root;}
async function exists(d,n){if(d.zip)return false;try{await d.getFileHandle(n);return true}catch{return false}}
async function put(d,n,blob,mtime){if(d.zip){d.zip.entries.push([d.rel.concat(n).join('/'),new Uint8Array(await blob.arrayBuffer()),mtime]);return;}const w=await (await d.getFileHandle(n,{create:true})).createWritable();await w.write(blob);await w.close();}

/* ---- zip (store-only) for browsers that can't write to a folder ---- */
const CRC=(()=>{const t=[];for(let n=0;n<256;n++){let c=n;for(let k=0;k<8;k++)c=c&1?0xEDB88320^(c>>>1):c>>>1;t[n]=c>>>0;}return t;})();
const crc=b=>{let c=~0;for(let i=0;i<b.length;i++)c=CRC[(c^b[i])&255]^(c>>>8);return ~c>>>0;};
function zip(entries){
  const enc=new TextEncoder(),parts=[],cd=[];let off=0;
  for(const [name,data,mt] of entries){
    const n=enc.encode(name),c=crc(data),h=new DataView(new ArrayBuffer(30)),D=new Date(mt||Date.now()),tm=D.getHours()<<11|D.getMinutes()<<5|D.getSeconds()>>1,dt=(Math.max(D.getFullYear(),1980)-1980)<<9|(D.getMonth()+1)<<5|D.getDate();
    h.setUint32(0,0x04034b50,true);h.setUint16(4,20,true);h.setUint16(6,0x800,true);h.setUint16(10,tm,true);h.setUint16(12,dt,true);h.setUint32(14,c,true);h.setUint32(18,data.length,true);h.setUint32(22,data.length,true);h.setUint16(26,n.length,true);
    parts.push(h.buffer,n,data);
    const d=new DataView(new ArrayBuffer(46));
    d.setUint32(0,0x02014b50,true);d.setUint16(4,20,true);d.setUint16(6,20,true);d.setUint16(8,0x800,true);d.setUint16(12,tm,true);d.setUint16(14,dt,true);d.setUint32(16,c,true);d.setUint32(20,data.length,true);d.setUint32(24,data.length,true);d.setUint16(28,n.length,true);d.setUint32(42,off,true);
    cd.push(d.buffer,n);off+=30+n.length+data.length;
  }
  const size=cd.reduce((a,x)=>a+(x.byteLength??x.length),0),e=new DataView(new ArrayBuffer(22));
  e.setUint32(0,0x06054b50,true);e.setUint16(8,entries.length,true);e.setUint16(10,entries.length,true);e.setUint32(12,size,true);e.setUint32(16,off,true);
  return new Blob([...parts,...cd,e.buffer],{type:'application/zip'});
}
function download(blob,name){const a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download=name;a.click();setTimeout(()=>URL.revokeObjectURL(a.href),6e4);}

/* ---- oxipng (WASM, in workers) ---- */
const OXW=[],OXP=new Map();let oxN=0,oxId=0;
function oxi(bytes){
  if(!OXW.length)for(let k=0;k<Math.min(8,navigator.hardwareConcurrency||4);k++){
    const w=new Worker(new URL('oxipng/worker.js',location.href),{type:'module'});
    w.onmessage=e=>{const p=OXP.get(e.data.id);OXP.delete(e.data.id);e.data.err?p.rej(new Error(e.data.err)):p.res(e.data.out);};OXW.push(w);
  }
  return new Promise((res,rej)=>{const id=++oxId;OXP.set(id,{res,rej});OXW[oxN++%OXW.length].postMessage({id,bytes,level:4},[bytes.buffer]);});
}

/* ---- PNG metadata -> JPEG (EXIF, ICC profile, 'parameters' text as EXIF UserComment) ---- */
const inflate=async b=>new Uint8Array(await new Response(new Blob([b]).stream().pipeThrough(new DecompressionStream('deflate'))).arrayBuffer());
const nul=(b,from)=>b.indexOf(0,from);
async function pngMeta(b){
  const r={exif:null,icc:null,params:null},dec=(x,enc='latin1')=>new TextDecoder(enc).decode(x);
  for(let o=8;o+12<=b.length;){
    const len=new DataView(b.buffer,b.byteOffset+o).getUint32(0),t=dec(b.subarray(o+4,o+8)),d=b.subarray(o+8,o+8+len);o+=12+len;
    try{
      if(t==='eXIf')r.exif=d;
      else if(t==='iCCP'){const k=nul(d,0);r.icc=await inflate(d.subarray(k+2));}
      else if(t==='tEXt'||t==='zTXt'||t==='iTXt'){
        const k=nul(d,0),key=dec(d.subarray(0,k));if(key!=='parameters')continue;
        if(t==='tEXt')r.params=dec(d.subarray(k+1));
        else if(t==='zTXt')r.params=dec(await inflate(d.subarray(k+2)));
        else{const flag=d[k+1];let q=nul(d,k+3);q=nul(d,q+1);const x=d.subarray(q+1);r.params=dec(flag?await inflate(x):x,'utf-8');}
      }
    }catch(e){}
  }
  return r;
}
function exifFromParams(text){
  const ascii=/^[\x00-\x7f]*$/.test(text),
        body=ascii?Uint8Array.from(text,c=>c.charCodeAt(0)):(()=>{const u=new Uint8Array(text.length*2);for(let i=0;i<text.length;i++){u[2*i]=text.charCodeAt(i)&255;u[2*i+1]=text.charCodeAt(i)>>8;}return u;})(),
        head=new TextEncoder().encode(ascii?'ASCII\0\0\0':'UNICODE\0'),n=head.length+body.length,out=new Uint8Array(44+n),v=new DataView(out.buffer);
  out.set([0x49,0x49,0x2a,0,8,0,0,0]);v.setUint16(8,1,true);v.setUint16(10,0x8769,true);v.setUint16(12,4,true);v.setUint32(14,1,true);v.setUint32(18,26,true);v.setUint32(22,0,true);
  v.setUint16(26,1,true);v.setUint16(28,0x9286,true);v.setUint16(30,7,true);v.setUint32(32,n,true);v.setUint32(36,44,true);v.setUint32(40,0,true);
  out.set(head,44);out.set(body,44+head.length);return out;
}
async function withMeta(jpeg,m){
  const segs=[],seg=(mk,payload)=>{const s=new Uint8Array(4+payload.length);s[0]=255;s[1]=mk;s[2]=(payload.length+2)>>8;s[3]=(payload.length+2)&255;s.set(payload,4);segs.push(s);};
  const exif=m.exif||(m.params?exifFromParams(m.params):null);
  if(exif&&exif.length<65000)seg(0xE1,new Uint8Array([69,120,105,102,0,0,...exif]));
  if(m.icc){const CH=65000,n=Math.ceil(m.icc.length/CH);for(let i=0;i<n;i++)seg(0xE2,new Uint8Array([...[...'ICC_PROFILE\0'].map(c=>c.charCodeAt(0)),i+1,n,...m.icc.subarray(i*CH,(i+1)*CH)]));}
  const keep=[jpeg.subarray(0,2)];   // drop the sRGB profile Chrome adds on its own
  for(let o=2;o<jpeg.length;){
    if(jpeg[o]!==255||jpeg[o+1]===0xDA){keep.push(jpeg.subarray(o));break;}
    const len=(jpeg[o+2]<<8|jpeg[o+3])+2;
    if(!(jpeg[o+1]===0xE2&&jpeg[o+4]===73&&jpeg[o+5]===67&&jpeg[o+6]===67))keep.push(jpeg.subarray(o,o+len));   // "ICC"
    o+=len;
  }
  jpeg=new Uint8Array(await new Blob(keep).arrayBuffer());
  if(!segs.length)return new Blob([jpeg],{type:'image/jpeg'});
  let at=2;if(jpeg[2]===255&&jpeg[3]===0xE0)at=4+((jpeg[4]<<8)|jpeg[5]);   // after JFIF
  return new Blob([jpeg.subarray(0,at),...segs,jpeg.subarray(at)],{type:'image/jpeg'});
}

/* ---- images ---- */
async function image(file,mode,q){
  const bytes=new Uint8Array(await file.arrayBuffer());
  if(mode==='png'){   // lossless: oxipng -o4 keeping every chunk, exactly like the app
    const out=await oxi(bytes.slice());
    return out.length<file.size?{blob:new Blob([out],{type:'image/png'}),flat:false,ext:'.png',meta:true}:{blob:file,flat:false,ext:'.png',meta:true};
  }
  const meta=await pngMeta(bytes),bmp=await createImageBitmap(file,{colorSpaceConversion:'none'}),c=new OffscreenCanvas(bmp.width,bmp.height),x=c.getContext('2d');
  x.drawImage(bmp,0,0);const d=x.getImageData(0,0,c.width,c.height).data;let flat=false;
  for(let i=3;i<d.length;i+=4)if(d[i]<255){flat=true;break;}
  x.globalCompositeOperation='destination-over';x.fillStyle='#fff';x.fillRect(0,0,c.width,c.height);
  const jpg=new Uint8Array(await (await c.convertToBlob({type:'image/jpeg',quality:q/100})).arrayBuffer());
  return {blob:await withMeta(jpg,meta),flat,ext:'.jpg',meta:!!(meta.exif||meta.icc||meta.params),params:!!meta.params};
}

/* ---- video (ffmpeg.wasm, single thread, H.264) ---- */
let FF=null,ffDur=0;
async function ff(){
  if(FF)return FF;
  const f=new FFmpegWASM.FFmpeg();
  f.on('log',({message})=>{const m=/Duration: (\d+):(\d+):([\d.]+)/.exec(message);if(m)ffDur=+m[1]*3600+ +m[2]*60+ +m[3];});
  f.on('progress',({progress})=>{CUR=Math.max(0,Math.min(1,progress));});
  const b=new URL('ffmpeg/',location.href).href,mt=self.crossOriginIsolated?b+'mt/':b;   // threads need SharedArrayBuffer (cross-origin isolation)
  await f.load({classWorkerURL:b+'814.ffmpeg.js',coreURL:mt+'ffmpeg-core.js',wasmURL:mt+'ffmpeg-core.wasm',...(mt!==b&&{workerURL:mt+'ffmpeg-core.worker.js'})});
  return FF=f;
}
async function video(file,o){
  if(o.codec!=='h264'||o.engine==='gpu'){if(await canWC(o))try{return await webcodecs(file,o);}catch(e){if(o.codec!=='h264'||cancel)throw e;console.warn('WebCodecs failed, using ffmpeg:',e.message);}else if(o.codec!=='h264')throw new Error('codec not available in this browser');}
  const f=await ff(),inn='in'+ext(file.name),outn='out.'+o.container;ffDur=0;CUR=0;
  await f.writeFile(inn,new Uint8Array(await file.arrayBuffer()));
  const vf=[];
  if(o.scale!=='orig')vf.push(`scale=-2:'min(${o.scale},ih)'`);
  if(o.fps!=='orig')vf.push(`fps=fps='min(${o.fps},source_fps)'`);
  const preset={fast:'veryfast',balanced:'medium',slow:'slow'}[o.speed]||'medium';
  let rate=['-crf',String(o.crf)];
  if(o.target_mb){   // need the duration first: a cheap probe run
    await f.exec(['-i',inn,'-t','0.01','-f','null','-']);
    const kbps=Math.max(50,Math.floor(o.target_mb*8192*.95/(ffDur||1))-(o.audio==='copy'?128:+o.audio.slice(3)));
    rate=['-b:v',kbps+'k','-maxrate',kbps*2+'k','-bufsize',kbps*4+'k'];
  }
  const au=o.audio==='copy'?['-c:a','copy']:['-c:a','aac','-b:a',o.audio.slice(3)+'k'];
  const args=['-i',inn,'-map','0:v:0','-map','0:a?','-c:v','libx264',...rate,'-preset',preset,'-pix_fmt','yuv420p',...(vf.length?['-vf',vf.join(',')]:[]),...au,'-y',outn];
  const rc=await f.exec(args);
  if(rc!==0)throw new Error('ffmpeg '+rc);
  const data=await f.readFile(outn);await f.deleteFile(inn).catch(()=>{});await f.deleteFile(outn).catch(()=>{});
  return new Blob([data]);
}

/* ---- video via WebCodecs (mediabunny): AV1, H.265 and hardware encoding. Bitrate-based, so CRF is mapped to a bitrate ---- */
let MB=null,STOP=null;const mb=async()=>MB||(MB=await import('./mediabunny.mjs'));
const MBC={av1:'av1',h265:'hevc',h264:'avc'};
// rough bits per pixel per frame at each codec's default quality; +/-6 CRF (8 for AV1) doubles/halves it
const BPP={av1:[0.05,42,8],h265:[0.06,28,6],h264:[0.09,23,6]};
async function canWC(o){const M=await mb();return M.canEncodeVideo(MBC[o.codec],{width:1280,height:720});}
async function webcodecs(file,o){
  const M=await mb(),input=new M.Input({source:new M.BlobSource(file),formats:M.ALL_FORMATS});
  const vt=await input.getPrimaryVideoTrack();if(!vt)throw new Error('no video track');
  const dur=await input.computeDuration()||1,st=await vt.computePacketStats(300),srcFps=st.averagePacketRate||30;
  let w=vt.displayWidth,h=vt.displayHeight;
  if(o.scale!=='orig'&&h>+o.scale){w=Math.round(w*+o.scale/h/2)*2;h=+o.scale;}
  const fps=o.fps!=='orig'&&srcFps>+o.fps?+o.fps:0,outFps=fps||srcFps,audioBps=o.audio==='copy'?128e3:+o.audio.slice(3)*1e3;
  const [base,def,step]=BPP[o.codec];
  let bitrate=o.target_mb?o.target_mb*8*1048576*.93/dur-audioBps:base*w*h*outFps*Math.pow(2,(def-o.crf)/step);
  bitrate=Math.round(Math.max(50e3,Math.min(bitrate,file.size*8/dur*.95)));   // never bigger than the source
  const aac=o.audio!=='copy'&&await M.canEncodeAudio('aac');
  let blob;
  for(let attempt=0;attempt<2;attempt++){
    const output=new M.Output({format:o.container==='mkv'?new M.MkvOutputFormat():new M.Mp4OutputFormat(),target:new M.BufferTarget()});
    const conv=await M.Conversion.init({input,output,
      video:{codec:MBC[o.codec],bitrate,forceTranscode:true,hardwareAcceleration:o.engine==='gpu'?'prefer-hardware':o.codec==='h265'?'no-preference':'prefer-software',...(o.scale!=='orig'&&{width:w,height:h,fit:'fill'}),...(fps&&{frameRate:fps})},
      audio:o.audio==='copy'||!aac?{}:{codec:'aac',bitrate:audioBps}});
    if(!conv.isValid)throw new Error('conversion not possible: '+conv.discardedTracks.map(t=>t.reason).join(','));
    conv.onProgress=p=>{CUR=p;};STOP=()=>conv.cancel();
    await conv.execute();STOP=null;
    blob=new Blob([output.target.buffer]);
    if(!o.target_mb||blob.size<=o.target_mb*1048576||attempt)break;
    bitrate=Math.round(Math.max(50e3,bitrate*o.target_mb*1048576*.95/blob.size));   // overshot: one more pass with a corrected bitrate
  }
  return blob;
}

/* ---- jobs ---- */
async function pool(items,n,fn){let i=0;await Promise.all(Array.from({length:Math.max(1,n)},async()=>{while(i<items.length&&!cancel){const it=items[i++];await fn(it);}}));}
async function run(b){
  S={...BLANK(),status:'running',kind:b.kind,start:Date.now()/1000};cancel=false;CUR=0;
  const dst=outDir()||H.inp,files=await walk(H.inp,b.kind,outDir());S.total=files.length;
  const items=files.filter(f=>!isVid(f.name)),vids=files.filter(f=>isVid(f.name));
  const one=async it=>{
    try{
      const file=await it.h.getFile();S.in_bytes+=file.size;
      let blob,name;
      if(isVid(it.name)){
        name=it.name.replace(/\.\w+$/,'')+'.'+b.video.container;
        const d=await dirAt(dst,it.rel);
        if(!b.overwrite&&await exists(d,name)){S.skipped++;S.out_bytes+=file.size;S.done++;return;}
        blob=await video(file,b.video);
        if(b.video.target_mb&&blob.size>b.video.target_mb*1048576)S.overtarget++;
        await put(d,name,blob,b.keep_dates?file.lastModified:0);S.converted++;
      }else{
        const r=await image(file,b.mode,b.quality);blob=r.blob;name=it.name.replace(/\.\w+$/,'')+r.ext;
        const d=await dirAt(dst,it.rel);
        if(!b.overwrite&&await exists(d,name)){S.skipped++;S.out_bytes+=file.size;S.done++;return;}
        await put(d,name,blob,b.keep_dates?file.lastModified:0);if(r.flat)S.flattened++;if(b.mode==='png'||r.params)S.verified++;else S.noparams++;
      }
      S.out_bytes+=blob.size;
      if(b.delete_orig&&!(dst===H.inp&&name===it.name))await it.dir?.removeEntry(it.name).catch(()=>{});
    }catch(e){console.error(e);if(!cancel)S.problems++;}
    S.done++;
  };
  await pool(items,b.workers,one);await pool(vids,1,one);
  if(H.out.zip&&H.out.entries.length){download(zip(H.out.entries),'compressed.zip');H.out.entries=[];}
  S.status=cancel?'cancelled':'done';S.end=Date.now()/1000;
}
async function estimate(b){
  EST={status:'running'};
  try{
    const files=await walk(H.inp,b.kind,outDir()),im=files.filter(f=>!isVid(f.name)),vd=files.filter(f=>isVid(f.name)),res={status:'done',in_bytes:0,out_bytes:0,sec:0};
    if(im.length){
      const smp=im.slice(0,3);let i=0,o=0,t0=performance.now();
      for(const s of smp){const f=await s.h.getFile();i+=f.size;o+=(await image(f,b.mode,b.quality)).blob.size;}
      const k=im.length/smp.length,tot=(await Promise.all(im.map(x=>x.h.getFile()))).reduce((a,f)=>a+f.size,0);
      const r=i?o/i:1,sec=(performance.now()-t0)/1000*k/Math.max(1,b.workers);
      res.images={count:im.length,in_bytes:tot,out_bytes:tot*r,sec};res.in_bytes+=tot;res.out_bytes+=tot*r;res.sec+=sec;
    }
    if(vd.length){
      const tot=(await Promise.all(vd.map(x=>x.h.getFile()))).reduce((a,f)=>a+f.size,0),r=b.video.target_mb?0.5:0.6,sec=tot/1e6*3;   // rough: no sample encode in the demo
      res.videos={count:vd.length,in_bytes:tot,out_bytes:tot*r,sec};res.in_bytes+=tot;res.out_bytes+=tot*r;res.sec+=sec;
    }
    EST=res;
  }catch(e){console.error(e);EST={status:'error'};}
}

/* grey out what this browser can't encode, and rename the GPU option */
(async()=>{
  const g=document.querySelector('#vEngine option[value=gpu]');if(g)g.textContent='Hardware encoder (GPU, faster, larger files)';
  for(const c of ['av1','h265']){
    if(await canWC({codec:c}).catch(()=>false))continue;
    const r=document.querySelector(`input[name=codec][value=${c}]`),l=r?.closest('label');if(!l)continue;
    r.disabled=true;l.style.opacity=.5;l.querySelector('span').textContent='Not available in this browser (try Chrome or Edge).';
  }
})();

window.DEMO=async(path,body)=>{
  if(path==='/api/state'){const end=S.end||Date.now()/1000;return {...S,partial:S.status==='running'?CUR:0,elapsed:S.start?end-S.start:0,ffmpeg:true,est:EST};}
  if(path==='/api/pick'){
    if(!window.showDirectoryPicker){   // Firefox/Safari: read a folder via <input webkitdirectory>, hand results back as a ZIP
      if(/output/i.test(body.title)){H.out={zip:true,entries:[],name:'Download as ZIP'};return {path:H.out.name};}
      const i=document.getElementById('demoDir')||Object.assign(document.body.appendChild(document.createElement('input')),{id:'demoDir',type:'file',hidden:true,webkitdirectory:true,multiple:true});
      return new Promise(res=>{i.value='';i.onchange=()=>{const f=[...i.files];if(!f.length)return res({path:''});H.inp={fb:true,files:f,name:f[0].webkitRelativePath.split('/')[0]};H.out=H.out?.zip?H.out:null;const d=document.getElementById('del');if(d){d.checked=false;d.disabled=true;}res({path:H.inp.name});};i.click();});
    }
    try{const h=await showDirectoryPicker({mode:'readwrite'}),w=/output/i.test(body.title)?'out':'inp';H[w]=h;return {path:h.name};}catch{return {path:''};}
  }
  if(path==='/api/scan'){
    const ok=H.inp&&body.input===H.inp.name;if(!ok)return {valid:false,count:0,bytes:0};
    if(body.output&&body.output===body.input)H.out=same();
    const fs=await walk(H.inp,body.kind,outDir());let bytes=0;for(const f of fs)bytes+=(await f.h.getFile()).size;
    return {valid:true,count:fs.length,bytes};
  }
  if(path==='/api/start'||path==='/api/estimate'){
    if(!H.inp||body.input!==H.inp.name)throw new Error('input');
    if(body.output===body.input)H.out=same();else if(!H.out||body.output!==H.out.name)throw new Error('output');
    if(path==='/api/start'&&body.kind!=='images'&&body.video.codec!=='h264'&&!await canWC(body.video)){
      alert((body.video.codec==='av1'?'AV1':'H.265')+' encoding is not available in this browser (Chrome or Edge on a computer, with hardware support for H.265). Pick H.264, or use the Windows app.');throw new Error('codec');}
    (path==='/api/start'?run:estimate)(body);return {ok:true};
  }
  if(path==='/api/cancel'){cancel=true;STOP?.();return {ok:true};}
  return {};
};
})();

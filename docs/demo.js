/* Browser-side stand-in for server.py: same /api/* contract, runs entirely in this tab.
   Folders: File System Access API (Chrome/Edge). Images: canvas. Video: ffmpeg.wasm (H.264 only). */
(()=>{
const VEXT=['.mp4','.mkv','.mov','.avi','.webm','.m4v','.wmv','.flv'];
const H={inp:null,out:null};
const BLANK=()=>({status:'idle',kind:'images',total:0,done:0,partial:0,start:0,end:0,in_bytes:0,out_bytes:0,converted:0,verified:0,noparams:0,flattened:0,skipped:0,problems:0,overtarget:0,error:''});
let S=BLANK(), EST={status:'idle'}, cancel=false, CUR=0;
const ext=n=>{const i=n.lastIndexOf('.');return i<0?'':n.slice(i).toLowerCase()};
const want=(n,kind)=>{const e=ext(n);return (kind!=='videos'&&e==='.png')||(kind!=='images'&&VEXT.includes(e))};
const isVid=n=>VEXT.includes(ext(n));

async function walk(dir,kind,skipDir,rel=[],out=[]){
  for await(const [name,h] of dir.entries()){
    if(h.kind==='directory'){ if(skipDir&&await h.isSameEntry(skipDir))continue; await walk(h,kind,skipDir,[...rel,name],out); }
    else if(want(name,kind)&&!name.toLowerCase().includes('.part.'))out.push({dir,rel,name,h});
  }
  return out;
}
const outDir=()=>H.out&&H.inp&&H.out===H.inp?null:H.out;
async function dirAt(root,rel){for(const n of rel)root=await root.getDirectoryHandle(n,{create:true});return root;}
async function exists(d,n){try{await d.getFileHandle(n);return true}catch{return false}}
async function put(d,n,blob){const w=await (await d.getFileHandle(n,{create:true})).createWritable();await w.write(blob);await w.close();}

/* ---- images ---- */
async function image(file,mode,q){
  const bmp=await createImageBitmap(file),c=new OffscreenCanvas(bmp.width,bmp.height),x=c.getContext('2d');
  let flat=false;
  if(mode==='jpg'){
    x.drawImage(bmp,0,0);const d=x.getImageData(0,0,c.width,c.height).data;
    for(let i=3;i<d.length;i+=4)if(d[i]<255){flat=true;break;}
    x.globalCompositeOperation='destination-over';x.fillStyle='#fff';x.fillRect(0,0,c.width,c.height);
    return {blob:await c.convertToBlob({type:'image/jpeg',quality:q/100}),flat,ext:'.jpg'};
  }
  x.drawImage(bmp,0,0);const blob=await c.convertToBlob({type:'image/png'});
  return blob.size<file.size?{blob,flat,ext:'.png'}:{blob:file,flat,ext:'.png'};   // keep the original if re-encoding doesn't help
}

/* ---- video (ffmpeg.wasm, single thread, H.264) ---- */
let FF=null,ffDur=0;
async function ff(){
  if(FF)return FF;
  const f=new FFmpegWASM.FFmpeg();
  f.on('log',({message})=>{const m=/Duration: (\d+):(\d+):([\d.]+)/.exec(message);if(m)ffDur=+m[1]*3600+ +m[2]*60+ +m[3];});
  f.on('progress',({progress})=>{CUR=Math.max(0,Math.min(1,progress));});
  const b=new URL('ffmpeg/',location.href).href;
  await f.load({classWorkerURL:b+'814.ffmpeg.js',coreURL:b+'ffmpeg-core.js',wasmURL:b+'ffmpeg-core.wasm'});
  return FF=f;
}
async function video(file,o){
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
        await put(d,name,blob);S.converted++;
      }else{
        const r=await image(file,b.mode,b.quality);blob=r.blob;name=it.name.replace(/\.\w+$/,'')+r.ext;
        const d=await dirAt(dst,it.rel);
        if(!b.overwrite&&await exists(d,name)){S.skipped++;S.out_bytes+=file.size;S.done++;return;}
        await put(d,name,blob);if(r.flat)S.flattened++;S.noparams++;   // canvas drops metadata
      }
      S.out_bytes+=blob.size;
      if(b.delete_orig&&!(dst===H.inp&&name===it.name))await it.dir.removeEntry(it.name).catch(()=>{});
    }catch(e){console.error(e);S.problems++;}
    S.done++;
  };
  await pool(items,b.workers,one);await pool(vids,1,one);
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

window.DEMO=async(path,body)=>{
  if(path==='/api/state'){const end=S.end||Date.now()/1000;return {...S,partial:S.status==='running'?CUR:0,elapsed:S.start?end-S.start:0,ffmpeg:true,est:EST};}
  if(path==='/api/pick'){
    if(!window.showDirectoryPicker){alert('The folder demo needs Chrome or Edge on a computer.');return {path:''};}
    try{const h=await showDirectoryPicker({mode:'readwrite'}),w=/output/i.test(body.title)?'out':'inp';H[w]=h;return {path:h.name};}catch{return {path:''};}
  }
  if(path==='/api/scan'){
    const ok=H.inp&&body.input===H.inp.name;if(!ok)return {valid:false,count:0,bytes:0};
    if(body.output&&body.output===body.input)H.out=H.inp;
    const fs=await walk(H.inp,body.kind,outDir());let bytes=0;for(const f of fs)bytes+=(await f.h.getFile()).size;
    return {valid:true,count:fs.length,bytes};
  }
  if(path==='/api/start'||path==='/api/estimate'){
    if(!H.inp||body.input!==H.inp.name)throw new Error('input');
    if(body.output===body.input)H.out=H.inp;else if(!H.out||body.output!==H.out.name)throw new Error('output');
    body.video={...body.video,codec:'h264',engine:'cpu'};
    (path==='/api/start'?run:estimate)(body);return {ok:true};
  }
  if(path==='/api/cancel'){cancel=true;return {ok:true};}
  return {};
};
})();

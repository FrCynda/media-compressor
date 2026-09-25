import init,{optimise} from './squoosh_oxipng.js';
const ready=init(new URL('squoosh_oxipng_bg.wasm',import.meta.url));
onmessage=async e=>{const {id,bytes,level}=e.data;await ready;
  try{const o=optimise(bytes,level,false,false);postMessage({id,out:o},[o.buffer]);}catch(err){postMessage({id,err:String(err)});}};

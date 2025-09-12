export class LivePoll{
  constructor(fn,intervalMs=30000){ this.fn=fn; this.ms=intervalMs; this._tm=null; this._dead=false; }
  async _tick(){ if(this._dead) return; try{ await this.fn(); }catch{} if(!this._dead) this._tm=setTimeout(()=>this._tick(),this.ms); }
  start(){ this._dead=false; if(!this._tm) this._tick(); }
  stop(){ this._dead=true; if(this._tm){ clearTimeout(this._tm); this._tm=null; } }
}

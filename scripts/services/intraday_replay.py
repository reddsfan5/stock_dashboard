"""分时页面共用的前端回放控制器与样式。

回放只控制“已经揭示到第几根分钟线”，具体图表和行情摘要仍由各页面渲染，
因此分时查询页和选股日记可以共用状态机而保持各自的展示结构。
"""

REPLAY_CSS = r"""
.intraday-replay{display:flex;align-items:center;gap:8px;flex-wrap:wrap;padding:9px 18px;border-top:1px solid var(--border,var(--line,#e2e7ef));border-bottom:1px solid var(--border,var(--line,#e2e7ef));background:#fafbfc}
.intraday-replay button,.intraday-replay select{height:32px;border:1px solid var(--border,var(--line,#d7dde7));border-radius:7px;background:#fff;color:var(--text,var(--ink,#202124));padding:0 10px;cursor:pointer}
.intraday-replay button:disabled{opacity:.42;cursor:not-allowed}.intraday-replay .replay-primary{background:var(--blue,#3478f6);border-color:var(--blue,#3478f6);color:#fff;font-weight:700;min-width:104px}
.intraday-replay .replay-clock{font-variant-numeric:tabular-nums;font-weight:750;color:var(--blue,#3478f6);min-width:42px;text-align:center}.intraday-replay .replay-progress{color:var(--muted,#758096);font-size:11px;margin-left:auto}
@media(max-width:700px){.intraday-replay{padding:8px 10px;gap:6px}.intraday-replay button,.intraday-replay select{height:30px;padding:0 8px;font-size:11px}.intraday-replay .replay-primary{min-width:94px}.intraday-replay .replay-progress{width:100%;margin-left:0}}
"""

REPLAY_JS = r"""
function createIntradayReplay({onFrame,intervalMs=450,initialSpeed=5}={}){
  let total=0,index=-1,dynamic=false,timer=null,speed=Math.max(1,+initialSpeed||1);
  const state=()=>({total,index,dynamic,playing:timer!==null,visibleCount:dynamic?Math.max(0,index+1):total,complete:dynamic&&total>0&&index>=total-1,speed});
  const emit=()=>{if(typeof onFrame==='function')onFrame(state())};
  const halt=()=>{if(timer!==null){clearTimeout(timer);timer=null}};
  const schedule=()=>{timer=setTimeout(()=>{timer=null;if(!dynamic||index>=total-1){emit();return}index=Math.min(index+speed,total-1);if(index<total-1)schedule();emit()},intervalMs)};
  return{
    load(count,{showAll=true}={}){halt();total=Math.max(0,Math.floor(+count||0));dynamic=!showAll;index=total?(showAll?total-1:0):-1;emit()},
    toggle(){if(timer!==null){halt();emit();return}if(!total)return;if(!dynamic||index>=total-1){dynamic=true;index=0}if(index<total-1)schedule();emit()},
    pause(){halt();emit()},
    step(){halt();if(!total)return;if(!dynamic||index>=total-1){dynamic=true;index=0}else index=Math.min(index+1,total-1);emit()},
    reset(){halt();if(!total)return;dynamic=true;index=0;emit()},
    showAll(){halt();dynamic=false;index=total?total-1:-1;emit()},
    setSpeed(value){speed=Math.max(1,Math.floor(+value||1));emit()},
    getState(){return state()}
  };
}
"""


def inject_intraday_replay(html: str) -> str:
    """把共享控制器注入自包含 HTML 模板。"""
    return (
        html.replace("__INTRADAY_REPLAY_CSS__", REPLAY_CSS)
        .replace("__INTRADAY_REPLAY_JS__", REPLAY_JS)
    )

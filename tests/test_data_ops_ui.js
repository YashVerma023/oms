// Data Operation UI, driven in jsdom against the real rendered page.
//   python tests/render_page.py /admin/data-ops /tmp/dops.html
//   node   tests/test_data_ops_ui.js
const fs=require("fs"),path=require("path"),assert=require("assert"),{JSDOM}=require("jsdom");
const ROOT="/sessions/wizardly-loving-mayer/mnt/omp";
const TABLES={tables:[{name:"algo_daily_pnl",rows:2},{name:"bhavcopy_nse_file",rows:9},{name:"carry_snapshots",rows:0}]};
const ROWS={table:"algo_daily_pnl",columns:["day","pnl","note"],total:3,limit:500,offset:0,
  rows:[["2026-08-21","100","good"],["2026-08-22","-50",""],["2026-08-23","250","good"]]};
const DESC={table:"algo_daily_pnl",rows:3,columns:[
  {name:"day",type:"date",nullable:false,key:true},
  {name:"pnl",type:"bigint",nullable:true,key:false},
  {name:"note",type:"varchar(255)",nullable:true,key:false}]};
const HTML=process.argv[2]||"/tmp/dops.html";
const dom=new JSDOM(fs.readFileSync(HTML,"utf8"),{runScripts:"outside-only",pretendToBeVisual:true});
const {window}=dom;
window.fetch=(url)=>{
  const body = url.indexOf("/rows")!==-1 ? ROWS : url.indexOf("/table/")!==-1 ? DESC : TABLES;
  return Promise.resolve({ok:true,json:()=>Promise.resolve(body)});
};
window.URL.createObjectURL=()=>"blob:x"; window.URL.revokeObjectURL=()=>{};
const conf=fs.readFileSync(HTML,"utf8").match(/window\.OMP_DATA_OPS\s*=\s*\{[\s\S]*?\};/);
window.eval(conf[0]);
["static/js/filters.js","static/js/data-ops.js"].forEach(f=>window.eval(fs.readFileSync(path.join(ROOT,f),"utf8")));
const $=id=>window.document.getElementById(id);
const side=n=>[...window.document.querySelectorAll(".side-item")].find(b=>b.dataset.section===n);
let bad=0;
const check=(l,g,w)=>{const ok=JSON.stringify(g)===JSON.stringify(w);
  console.log((ok?"PASS  ":"FAIL  ")+l+" -> "+JSON.stringify(g)+(ok?"":"  want "+JSON.stringify(w)));if(!ok)bad++;};
const type=(el,v)=>{el.value=v;el.dispatchEvent(new window.Event("input",{bubbles:true}));};

check("Add section header", $("sectionBlurb")===null && window.document.querySelector('[data-section="add"] .section-head h1').textContent.trim(), "Add Table to OMP database");
check("Alter header", window.document.querySelector('[data-section="alter"] .section-head h1').textContent.trim(), "Alter table");
check("blurb is centred by class", !!window.document.querySelector('[data-section="add"] .section-head .subtitle'), true);

side("tables").click();
setTimeout(()=>{
  check("tables listed", [...$("tablesBody").rows].map(r=>r.cells[0].textContent), ["algo_daily_pnl","bhavcopy_nse_file","carry_snapshots"]);
  check("each row has View and Manage", [...$("tablesBody").rows[0].querySelectorAll("button")].map(b=>b.textContent), ["View","Manage"]);
  type($("tableSearch"),"bhav");
  check("list is searchable", [...$("tablesBody").rows].map(r=>r.cells[0].textContent), ["bhavcopy_nse_file"]);
  type($("tableSearch"),"");

  side("alter").click();
  setTimeout(()=>{
    check("alter lists only the additional tables", [...$("alterBody").rows].map(r=>r.cells[0].textContent), ["algo_daily_pnl","bhavcopy_nse_file","carry_snapshots"]);
    side("tables").click();
    $("tablesBody").rows[0].querySelectorAll("button")[0].click();   // View
    setTimeout(()=>{
      check("the list is replaced, not appended", [$("tablesScreen").hidden,$("tableScreen").hidden], [true,false]);
      check("title is the table", $("detailTitle").textContent, "algo_daily_pnl");
      check("column headers", [...$("rowsHead").cells].map(c=>c.textContent), ["day","pnl","note"]);
      check("a filter box per column", $("rowsFilters").cells.length, 3);
      check("all rows shown", $("rowsBody").rows.length, 3);

      type($("rowsFilters").cells[2].querySelector("input"),"good");
      check("column filter narrows", $("rowsBody").rows.length, 2);
      type($("rowsFilters").cells[2].querySelector("input")," ");
      check("a space finds blanks", $("rowsBody").rows.length, 1);
      type($("rowsFilters").cells[2].querySelector("input"),"/");
      check("slash finds non-blanks", $("rowsBody").rows.length, 2);
      type($("rowsFilters").cells[2].querySelector("input"),"");
      type($("rowSearch"),"250");
      check("global search works", $("rowsBody").rows.length, 1);
      type($("rowSearch"),"");

      $("tabStructure").click();
      check("structure pane", [$("rowsPane").hidden,$("structurePane").hidden], [true,false]);
      check("structure rows", [...$("structureBody").rows].map(r=>r.cells[0].textContent), ["day","pnl","note"]);
      check("key flagged", $("structureBody").rows[0].cells[3].textContent, "Primary");

      $("btnBack").click();
      check("back returns to the list", [$("tablesScreen").hidden,$("tableScreen").hidden], [false,true]);
      console.log(bad?`\n${bad} FAILED`:"\nAll Data Operation UI checks passed");
      process.exit(bad?1:0);
    },30);
  },30);
},30);

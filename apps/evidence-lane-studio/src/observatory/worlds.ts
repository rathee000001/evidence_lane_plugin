/** Each selected Studio view inhabits its own coded composition in the blue observatory family. */
export const studioWorlds = {
  projects: {name:'Project constellation',accent:'#7cddff',id:0},
  plan: {name:'Ordered starlanes',accent:'#71ddff',id:1},
  jobs: {name:'Violet current',accent:'#ba99ff',id:2},
  workers: {name:'Process constellation',accent:'#77ebd0',id:3},
  evidence: {name:'Evidence nebula',accent:'#6ee9ee',id:4},
  tools: {name:'Amber orbit',accent:'#ffcf83',id:5},
  connections: {name:'Connected horizons',accent:'#75e4ff',id:6},
  learning: {name:'Violet aurora',accent:'#d2a0ff',id:7},
  accelerators: {name:'Compute corona',accent:'#8dbfff',id:8},
  diagnostics: {name:'Signal observatory',accent:'#ffabc5',id:9},
} as const;
export const studioWorld = (view:string) => studioWorlds[view as keyof typeof studioWorlds] ?? studioWorlds.projects;

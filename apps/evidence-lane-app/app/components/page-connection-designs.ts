import type {OpticalRouteDesign} from './home-optical-routes';

/** Visual grammar only: the owning story still supplies every relationship and transition. */
export const pageConnectionDesigns={
 product:{bands:3,threads:5,width:.12,spacing:.18,threadSpacing:.10,opacity:.30,threadOpacity:.36,colors:[0x9ecde4,0xb7b1db,0xbadfcf],motion:.7},
 workflows:{bands:1,threads:2,width:.46,threadSpacing:.44,opacity:.28,threadOpacity:.56,colors:[0x9ddce0],motion:.45},
 studio:{bands:1,threads:2,width:.20,threadSpacing:.22,opacity:.22,threadOpacity:.52,colors:[0x9acfe7,0xced5e8],motion:.3},
 'how-it-works':{bands:2,threads:3,width:.14,spacing:.13,threadSpacing:.13,opacity:.28,threadOpacity:.48,colors:[0x91dcd8,0xd1e5f2],motion:.35},
 integrations:{bands:2,threads:4,width:.11,spacing:.22,threadSpacing:.11,opacity:.22,threadOpacity:.42,colors:[0xa3d7ef,0xc6b9e4],motion:.25},
 docs:{bands:0,threads:3,threadSpacing:.13,threadOpacity:.50,colors:[0xaec9e3,0xd6dff0,0xbba8d8],motion:.2},
 download:{bands:1,threads:2,width:.40,threadSpacing:.39,opacity:.27,threadOpacity:.48,colors:[0xded2b0,0x92c7db],motion:.4},
} satisfies Record<string,OpticalRouteDesign>;

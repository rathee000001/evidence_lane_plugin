"""3D interpretation of the existing Evidence Lane cube faces and stripe marks."""
import bpy
from pathlib import Path
root=Path(__file__).resolve().parents[1]
bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
def material(name,color,metal=.25,rough=.2):
 m=bpy.data.materials.new(name);m.use_nodes=True;m.diffuse_color=(*color,1)
 p=m.node_tree.nodes.get('Principled BSDF');p.inputs['Base Color'].default_value=(*color,1);p.inputs['Metallic'].default_value=metal;p.inputs['Roughness'].default_value=rough;p.inputs['Coat Weight'].default_value=1
 return m
blue=material('Brand blue face',(.38,.64,.91));silver=material('Brand silver face',(.82,.83,.9));pearl=material('Brand pearl top',(.92,.92,.97));mark=material('Brand face marks',(.94,.98,1),.15,.17)
bpy.ops.mesh.primitive_cube_add(size=2);cube=bpy.context.object;cube.name='EvidenceLane_brand_cube'
for m in [blue,silver,pearl]:cube.data.materials.append(m)
for p in cube.data.polygons:p.material_index=2 if p.normal.z>.5 else 0 if p.normal.y<-.5 else 1
b=cube.modifiers.new('Shallow optical edge','BEVEL');b.width=.035;b.segments=4;cube.modifiers.new('Weighted face normals','WEIGHTED_NORMAL')
# Same quadrilateral markings as the retained CubeAppIcon SVG, mapped onto the true faces.
left=[[(57,119),(108,149),(108,166),(57,136)],[(59,157),(108,186),(108,203),(59,174)]]
right=[[(148,145),(203,113),(203,130),(148,162)],[(148,184),(203,152),(203,169),(148,201)]]
for side,marks in [('left',left),('right',right)]:
 for i,poly in enumerate(marks):
  verts=[]
  for x,y in poly:
   if side=='left':u=(x-32)/96;v=(y-76-56*u)/104;verts.append((-1+2*u,-1.006,1-2*v))
   else:u=(x-128)/96;v=(y-132+56*u)/104;verts.append((1.006,-1+2*u,1-2*v))
  mesh=bpy.data.meshes.new('Brand mark');mesh.from_pydata(verts,[],[(0,1,2,3)]);mesh.update();o=bpy.data.objects.new('Brand_'+side+'_mark_'+str(i),mesh);bpy.context.collection.objects.link(o);o.data.materials.append(mark)
  solid=o.modifiers.new('Raised enamel','SOLIDIFY');solid.thickness=.008
out=root/'public/product';art=root/'artwork/product';out.mkdir(parents=True,exist_ok=True);art.mkdir(parents=True,exist_ok=True)
bpy.ops.wm.save_as_mainfile(filepath=str(art/'evidence-lane-cube.blend'))
bpy.ops.export_scene.gltf(filepath=str(out/'evidence-lane-cube.glb'),export_format='GLB',export_cameras=False,export_lights=False)
print('PRODUCT_BRAND_CUBE_EXPORTED')

"""Original route-specific Blender sculptures; no downloaded models or textures."""
import bpy,math
from pathlib import Path
from mathutils import Vector
APP=Path(__file__).resolve().parents[1];OUT=APP/'public/scenes';ART=APP/'artwork';ART.mkdir(exist_ok=True)
def mat(name,color,metal=.6,emit=0):
 m=bpy.data.materials.new(name);m.diffuse_color=(*color,1);m.use_nodes=True;p=m.node_tree.nodes.get('Principled BSDF');p.inputs['Base Color'].default_value=(*color,1);p.inputs['Metallic'].default_value=metal;p.inputs['Roughness'].default_value=.25;p.inputs['Emission Color'].default_value=(*color,1);p.inputs['Emission Strength'].default_value=emit;return m
def cube(name,loc,scale,m):
 bpy.ops.mesh.primitive_cube_add(size=1,location=loc);o=bpy.context.object;o.name=name;o.scale=scale;bpy.ops.object.transform_apply(location=False,rotation=False,scale=True);o.data.materials.append(m);b=o.modifiers.new('Machined edges','BEVEL');b.width=.055;b.segments=3;o.modifiers.new('Normals','WEIGHTED_NORMAL');return o
def ring(name,r,z,m):
 bpy.ops.mesh.primitive_torus_add(major_radius=r,minor_radius=.02,major_segments=96,minor_segments=8,location=(0,0,z));o=bpy.context.object;o.name=name;o.data.materials.append(m);return o
for kind in ['atlas','studio','journey','harbor','library','hangar']:
 bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
 dark=mat('Midnight ceramic',(.024,.044,.075));white=mat('Porcelain',(.65,.8,.85));cyan=mat('Cyan light',(.04,.7,.85),.4,2);purple=mat('Violet light',(.36,.16,.8),.5,1.5)
 if kind=='atlas':
  for i in range(5):
   a=i*math.tau/5;x,y=3*math.cos(a),3*math.sin(a);cube('Island_%d'%i,(x,y,-.3),(1.8,1.6,.3),dark);cube('Evidence_%d'%i,(x,y,.1),(1.1,.9,.16),white);cube('Link_%d'%i,(x/2,y/2,-.1),(.04,3,.02),cyan).rotation_euler.z=a-math.pi/2
  ring('Center',.9,.1,cyan);cube('Selected_project',(0,0,.3),(1,1,.4),dark)
 elif kind=='studio':
  cube('Observatory_base',(0,0,-.4),(6,3.4,.25),dark)
  for x in [-2,0,2]:
   cube('Window_frame',(x,.5,1),(1.8,.16,2.5),white);cube('Window_display',(x,.39,1),(1.63,.035,2.3),dark)
   for z in [.4,.8,1.2,1.6]:cube('Observation',(x,.35,z),(1.25,.03,.03),cyan)
  ring('Observatory_signal',3,0,cyan)
 elif kind=='journey':
  for i in range(5):
   x=(i-2)*1.65;y=(i%2)*.7;cube('Stage_%d'%i,(x,y,-.15),(.95,1.35,.3),dark);cube('Waylight_%d'%i,(x,y,.07),(.85,.05,.03),cyan)
   cube('Step_%d'%i,(x,y,.3+i*.1),(.4,.5,.5+i*.15),white)
   if i<4:cube('Path_%d'%i,(x+.8,.35,-.1),(.85,.06,.025),cyan)
 elif kind=='harbor':
  cube('Harbor',(0,0,-.5),(7,1.2,.5),dark)
  for i in range(5):
   x=(i-2)*1.35;cube('Pier_%d'%i,(x,-1.15,-.25),(.65,2.2,.15),white);cube('Dock_%d'%i,(x,-2.1,.05),(.75,.75,.3),dark);cube('Connector_%d'%i,(x,-2.1,.4),(.3,.3,.4),cyan if i%2 else purple)
  for i in range(3):cube('Shoreline',(0,.6+i*.23,-.2),(7,.025,.02),cyan)
 elif kind=='library':
  for row in range(3):
   y=(row-1)*1.8;cube('Shelf',(0,y,-.2),(6,1.1,.2),dark)
   for col in range(7):
    x=(col-3)*.75;cube('Volume_%d_%d'%(row,col),(x,y,.5),(.32,.75,1.35),white if col%3 else dark);cube('Spine',(x,y-.39,.5),(.15,.025,.75),cyan if row%2 else purple)
 elif kind=='hangar':
  cube('Landing_deck',(0,0,-.4),(6,5,.25),dark)
  for i in range(3):
   y=(i-1)*1.4
   for x in [-2.2,2.2]:cube('Hangar_pillar',(x,y,1.1),(.16,.2,3),white)
   cube('Hangar_beam',(0,y,2.6),(4.6,.2,.16),white);cube('Ceiling_light',(0,y,2.48),(4,.05,.04),cyan)
  cube('Release_capsule',(0,0,.4),(1.6,2.2,1.25),white);cube('Release_seal',(0,-1.13,.5),(.9,.03,.12),cyan)
 scene=bpy.context.scene;scene.world.color=(.015,.02,.03)
 for loc,color,power in [((2,-5,8),(.65,.9,1),1500),((-5,3,4),(.4,.2,1),1800)]:
  bpy.ops.object.light_add(type='AREA',location=loc);o=bpy.context.object;o.data.energy=power;o.data.color=color;o.data.size=5;o.rotation_euler=(-o.location).to_track_quat('-Z','Y').to_euler()
 bpy.ops.object.camera_add(location=(8,-12,10));cam=bpy.context.object;cam.rotation_euler=(Vector((0,0,.3))-cam.location).to_track_quat('-Z','Y').to_euler();cam.data.type='ORTHO';cam.data.ortho_scale=10.5;scene.camera=cam
 scene.render.engine='CYCLES';scene.cycles.samples=16;scene.cycles.use_denoising=True;scene.render.film_transparent=True;scene.render.resolution_x=1200;scene.render.resolution_y=900;scene.render.resolution_percentage=100;scene.view_settings.view_transform='AgX'
 bpy.ops.wm.save_as_mainfile(filepath=str(ART/(kind+'.blend')))
 bpy.ops.export_scene.gltf(filepath=str(OUT/(kind+'.glb')),export_format='GLB',export_cameras=False,export_lights=False)
 scene.render.filepath=str(OUT/(kind+'.png'));bpy.ops.render.render(write_still=True)
 print('COMPLETED_SCENE',kind)

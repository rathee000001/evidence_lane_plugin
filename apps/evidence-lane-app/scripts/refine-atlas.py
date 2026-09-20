"""Blender-authored project atlas: five distinct instrument islands and connected paths."""
import bpy, math, sys
from pathlib import Path
from mathutils import Vector
APP=Path(__file__).resolve().parents[1];OUT=APP/'public/scenes';ART=APP/'artwork';KIND='journey' if '--journey' in sys.argv else 'atlas'
bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
def mat(name,c,metal=.3,emit=0):
 m=bpy.data.materials.new(name);m.use_nodes=True;m.diffuse_color=(*c,1);p=m.node_tree.nodes.get('Principled BSDF');p.inputs['Base Color'].default_value=(*c,1);p.inputs['Metallic'].default_value=metal;p.inputs['Roughness'].default_value=.24;p.inputs['Emission Color'].default_value=(*c,1);p.inputs['Emission Strength'].default_value=emit;return m
pearl=mat('Porcelain silver',(.58,.76,.83),.55);edge=mat('Titanium edge',(.10,.23,.34),.75);dark=mat('Instrument glass',(.035,.12,.18),.45);cyan=mat('Source cyan',(.04,.65,.85),.3,1.6);gold=mat('Result gold',(.85,.58,.17),.5,1.0);pink=mat('Memory rose',(.71,.28,.57),.4,1.2);green=mat('Work mint',(.18,.70,.49),.4,1.4);blue=mat('Plan iris',(.28,.36,.84),.4,1.4)
def box(name,loc,scale,m,bevel=.05):
 bpy.ops.mesh.primitive_cube_add(size=1,location=loc);o=bpy.context.object;o.name=name;o.scale=scale;bpy.ops.object.transform_apply(location=False,rotation=False,scale=True);o.data.materials.append(m);b=o.modifiers.new('Precision edge','BEVEL');b.width=bevel;b.segments=3;o.modifiers.new('Normals','WEIGHTED_NORMAL');return o
def cylinder(name,loc,r,depth,m,verts=64):
 bpy.ops.mesh.primitive_cylinder_add(vertices=verts,radius=r,depth=depth,location=loc);o=bpy.context.object;o.name=name;o.data.materials.append(m);b=o.modifiers.new('Soft edge','BEVEL');b.width=.035;b.segments=3;o.modifiers.new('Normals','WEIGHTED_NORMAL');return o
def torus(name,loc,r,m,thickness=.018):
 bpy.ops.mesh.primitive_torus_add(major_radius=r,minor_radius=thickness,major_segments=96,minor_segments=8,location=loc);o=bpy.context.object;o.name=name;o.data.materials.append(m);return o
def path(name,points,m,width=.012):
 c=bpy.data.curves.new(name,'CURVE');c.dimensions='3D';c.bevel_depth=width;c.bevel_resolution=3;s=c.splines.new('POLY');s.points.add(len(points)-1)
 for p,co in zip(s.points,points):p.co=(*co,1)
 o=bpy.data.objects.new(name,c);bpy.context.collection.objects.link(o);o.data.materials.append(m)
positions=[(-4,0),(-2,0),(0,1.5),(2,0),(4,0)] if KIND=='journey' else [(-3.2,1.2),(1.1,3.0),(3.45,-.2),(.9,-3.0),(-3,-2.3)]
colors=[cyan,blue,green,gold,pink]
for i,((x,y),accent) in enumerate(zip(positions,colors)):
 # Floating stepped platform with an illuminated seam, landing grid and corner hardware.
 for z,r,d,m in [(-.45,1.1,.18,edge),(-.30,1.13,.035,accent),(-.18,1.17,.22,pearl)]:cylinder('Island_%d'%i,(x,y,z),r,d,m,6)
 for k in range(6):
  a=k*math.tau/6;cylinder('Fastener',(x+.97*math.cos(a),y+.97*math.sin(a),-.045),.05,.035,edge,12)
 torus('Instrument bus',(x,y,-.04),.77,accent,.012)
 for k in [-1,0,1]:box('Contact rail',(x+k*.28,y-.70,-.015),(.14,.045,.02),edge,.008)
 if i==0:
  for j in range(4):
   z=.06+j*.13;o=box('Source document',(x-.22+j*.08,y+.10,z),(.85,1.05,.055),pearl);o.rotation_euler.z=-.22+j*.1
  for j in range(3):box('Text line',(x+.02,y+.28-j*.16,.49),(.47,.025,.014),cyan,.007)
  cylinder('Source locator',(x+.59,y-.37,.21),.17,.5,dark,32);torus('Locator glow',(x+.59,y-.37,.48),.17,cyan)
 elif i==1:
  box('Plan console',(x,y,.09),(1.17,.85,.2),dark)
  for j in range(4):
   box('Ordered task',(x-.35+j*.23,y,.25+j*.065),(.16,.55,.18+j*.11),pearl)
   box('Task highlight',(x-.35+j*.23,y-.29,.25+j*.065),(.12,.02,.08),blue,.006)
 elif i==2:
  cylinder('Work hub',(x,y,.21),.5,.43,dark);cylinder('Work cap',(x,y,.45),.42,.10,pearl)
  for j in range(8):
   a=j*math.tau/8;o=box('Worker module',(x+.65*math.cos(a),y+.65*math.sin(a),.22),(.24,.17,.36),pearl);o.rotation_euler.z=a
   box('Worker signal',(x+.65*math.cos(a),y+.65*math.sin(a),.42),(.12,.05,.02),green,.006)
  torus('Active work',(x,y,.58),.40,green)
 elif i==3:
  box('Outcome pedestal',(x,y,.05),(.9,.65,.17),dark)
  for dx in [-.45,.45]:box('Output frame',(x+dx,y,.60),(.06,.12,1.05),pearl)
  for dz in [.08,1.13]:box('Output frame',(x,y,dz),(.95,.12,.06),pearl)
  box('Outcome surface',(x,y+.02,.61),(.79,.035,.92),dark)
  for k in range(4):box('Verified result',(x,y-.005,.85-k*.16),(.52,.025,.03),gold,.006)
 else:
  for j in range(3):cylinder('Memory layers',(x,y,.07+j*.15),.48,.10,pearl);torus('Memory links',(x,y,.12+j*.15),.48,pink)
  bpy.ops.mesh.primitive_uv_sphere_add(segments=32,ring_count=16,radius=.32,location=(x,y,.64));bpy.context.object.data.materials.append(dark)
  for j in range(3):torus('Context orbit',(x,y,.64),.37,pink).rotation_euler=(j*math.pi/3,.8,0)
 # Paired curved illuminated paths connect each station to the central project.
 for offset,m in [(-.045,edge),(.045,accent)]:
  points=[]
  for k in range(41):
   t=k/40;points.append((x*t+offset,y*t,.08+math.sin(math.pi*t)*.28))
  path('Evidence pathway',points,m,.016 if m==edge else .009)
 # Beads identify progression without painting text into the model.
 for k in range(1,5):
  t=k/5;cylinder('Path marker',(x*t,y*t,.09+math.sin(math.pi*t)*.28),.035,.03,accent,12)
cylinder('Project foundation',(0,0,-.2),.77,.28,edge,6);cylinder('Project deck',(0,0,-.03),.82,.06,pearl,6)
torus('Project aperture',(0,0,.11),.59,cyan,.025)
for z in [.1,.24,.38]:o=box('Project core',(0,0,z),(.56,.56,.08),pearl);o.rotation_euler.z=math.pi/4
bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2,radius=.31,location=(0,0,.78));bpy.context.object.name='Project reference';bpy.context.object.data.materials.append(cyan)
scene=bpy.context.scene;scene.world.color=(.16,.20,.23)
for loc,color,power in [((2,-4,8),(.7,.9,1),1700),((-4,2,6),(.6,.7,1),1500),((1,5,4),(.5,1,.9),1000)]:
 bpy.ops.object.light_add(type='AREA',location=loc);o=bpy.context.object;o.data.energy=power;o.data.color=color;o.data.size=5;o.rotation_euler=(-o.location).to_track_quat('-Z','Y').to_euler()
bpy.ops.object.camera_add(location=(7,-10,13));cam=bpy.context.object;cam.rotation_euler=(Vector((0,0,0))-cam.location).to_track_quat('-Z','Y').to_euler();cam.data.type='ORTHO';cam.data.ortho_scale=11.8;scene.camera=cam
scene.render.engine='CYCLES';scene.cycles.samples=32;scene.cycles.use_denoising=True;scene.render.film_transparent=True;scene.render.resolution_x=1500;scene.render.resolution_y=1100;scene.render.resolution_percentage=100;scene.view_settings.view_transform='AgX'
bpy.ops.wm.save_as_mainfile(filepath=str(ART/(KIND+'.blend')));bpy.ops.export_scene.gltf(filepath=str(OUT/(KIND+'.glb')),export_format='GLB',export_cameras=False,export_lights=False);scene.render.filepath=str(OUT/(KIND+'.png'));bpy.ops.render.render(write_still=True)

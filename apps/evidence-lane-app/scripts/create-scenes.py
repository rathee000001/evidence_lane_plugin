"""Original Evidence Lane orbital sculpture. Run in Blender 5.1, no external assets."""
import bpy, math, random
from pathlib import Path
from mathutils import Vector

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'public' / 'scenes'
OUT.mkdir(parents=True, exist_ok=True)
ART = ROOT / 'artwork'
ART.mkdir(exist_ok=True)
bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete(use_global=False)
random.seed(42)

def material(name, color, metallic=.0, roughness=.35, emission=0):
    m = bpy.data.materials.new(name)
    m.diffuse_color = (*color, 1)
    m.use_nodes = True
    p = m.node_tree.nodes.get('Principled BSDF')
    p.inputs['Base Color'].default_value = (*color, 1)
    p.inputs['Metallic'].default_value = metallic
    p.inputs['Roughness'].default_value = roughness
    if emission:
        p.inputs['Emission Color'].default_value = (*color, 1)
        p.inputs['Emission Strength'].default_value = emission
    return m

dark = material('Midnight ceramic', (.62, .79, .87), .55, .2)
silver = material('Brushed titanium', (.10, .40, .55), .65, .25)
cyan = material('Evidence cyan', (.03, .72, .9), .55, .2, 2.5)
violet = material('Memory iris', (.38, .18, .95), .5, .22, 1.7)
white = material('Porcelain', (.2, .72, .86), .5, .18)

def cube(name, loc, scale, mat, bevel=.08):
    bpy.ops.mesh.primitive_cube_add(size=1, location=loc)
    o=bpy.context.object; o.name=name; o.scale=scale
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    o.data.materials.append(mat)
    mod=o.modifiers.new('Soft machined edges','BEVEL'); mod.width=bevel; mod.segments=3
    o.modifiers.new('Weighted corner normals','WEIGHTED_NORMAL')
    return o

def ring(name, radius, z, mat, tilt=0, thickness=.025):
    bpy.ops.mesh.primitive_torus_add(major_radius=radius, minor_radius=thickness, major_segments=128, minor_segments=8, location=(0,0,z))
    o=bpy.context.object; o.name=name; o.rotation_euler=(tilt,tilt*.4,0); o.data.materials.append(mat)
    return o

for i,(radius,z) in enumerate([(2.3,0),(3.5,.1),(4.5,.2)]):
    ring('Orbit_%d'%i,radius,z,silver,.1*i,.017)
    ring('Lightpath_%d'%i,radius+.035,z,cyan if i!=1 else violet,.1*i,.009)

for i in range(15):
    a=i*math.tau/15
    radius=[2.3,3.5,4.5][i%3]
    x,y=math.cos(a)*radius,math.sin(a)*radius
    z=random.uniform(-.3,.5)
    parent=bpy.data.objects.new('Fragment_%02d'%i,None); bpy.context.collection.objects.link(parent)
    parent.location=(x,y,z); parent.rotation_euler=(random.uniform(-.15,.15),random.uniform(-.1,.1),a+.3)
    body=cube('Source tile',(0,0,0),(.7,.9,.13),dark)
    body.parent=parent
    for j in range(3):
        line=cube('Source inscription',(-.08,.19-j*.14,.075),(.38 if j<2 else .23,.023,.014),cyan if i%3==0 else white,.007)
        line.parent=parent
    mark=cube('Source beacon',(.2,.31,.077),(.065,.065,.02),violet if i%3 else cyan,.01); mark.parent=parent

for z,s in [(-.45,1.35),(-.27,1.2),(-.08,1.05)]:
    o=cube('Project platform',(0,0,z),(s,s,.12),dark); o.rotation_euler.z=math.pi/4
ring('Project halo',.92,.3,cyan,0,.024)
bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=1,radius=.69,location=(0,0,.64))
core=bpy.context.object; core.name='Project_core'; core.scale=(.8,.8,1.3); core.data.materials.append(white)
wire=core.modifiers.new('Crystal facets','BEVEL');wire.width=.022;wire.segments=2
for i in range(38):
    a=random.uniform(0,math.tau); r=random.uniform(1.2,5)
    bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=1,radius=random.uniform(.015,.036),location=(r*math.cos(a),r*math.sin(a),random.uniform(-.2,.8)))
    bpy.context.object.name='Particle_%02d'%i; bpy.context.object.data.materials.append(cyan if i%3 else violet)

scene=bpy.context.scene
scene.world.color=(.015,.018,.028)
for name,loc,power,color,size in [('Key',(2,-3,7),1800,(.55,.85,1),5),('Rim',(-4,1,5),2300,(.4,.2,1),4),('Fill',(1,5,3),1800,(.2,1,1),3)]:
    bpy.ops.object.light_add(type='AREA',location=loc)
    o=bpy.context.object;o.name=name;o.data.energy=power;o.data.color=color;o.data.shape='DISK';o.data.size=size
    o.rotation_euler=(Vector((0,0,0))-o.location).to_track_quat('-Z','Y').to_euler()
bpy.ops.object.camera_add(location=(8,-11,12))
camera=bpy.context.object; camera.rotation_euler=(Vector((0,0,.1))-camera.location).to_track_quat('-Z','Y').to_euler()
camera.data.type='ORTHO';camera.data.ortho_scale=12.3;scene.camera=camera
scene.render.engine='CYCLES';scene.cycles.samples=24
scene.cycles.use_denoising=True
scene.render.resolution_x=1500;scene.render.resolution_y=1250;scene.render.resolution_percentage=100
scene.render.film_transparent=True
scene.render.image_settings.file_format='PNG'
scene.view_settings.view_transform='AgX'
bpy.ops.wm.save_as_mainfile(filepath=str(ART/'evidence-orbit.blend'))
bpy.ops.export_scene.gltf(filepath=str(OUT/'evidence-orbit.glb'),export_format='GLB',export_cameras=False,export_lights=False)
scene.render.filepath=str(OUT/'evidence-orbit.png')
bpy.ops.render.render(write_still=True)
print('EVIDENCE_SCENES_COMPLETE')

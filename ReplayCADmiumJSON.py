# Fusion 360: Replay a CADmium / Fusion360-ds JSON row into real geometry
# Draws circle / ring sketches, extrudes (NewBody/Join), flips on 180° about X,
# and TRANSLATES the body by tz instead of creating an offset construction plane.

import adsk.core, adsk.fusion, traceback, json, math

# -------- Adjust units here ----------
# If JSON is in meters -> Fusion cm: 100.0
# If JSON is in millimeters -> Fusion cm: 0.1
UNIT_MULT = 100
# -------------------------------------

def _get_ui_app_handlers():
    app = adsk.core.Application.get()
    ui  = app.userInterface
    return app, ui

def _map_operation(op_str):
    ops = adsk.fusion.FeatureOperations
    m = {
        'NewBodyFeatureOperation': ops.NewBodyFeatureOperation,
        'JoinFeatureOperation':    ops.JoinFeatureOperation,
        'CutFeatureOperation':     ops.CutFeatureOperation,
        'IntersectFeatureOperation': ops.IntersectFeatureOperation
    }
    return m.get(op_str, ops.NewBodyFeatureOperation)

def _pick_profile_for_circles(sketch):
    profs = sketch.profiles
    if profs.count == 0:
        return None
    # Prefer an annulus (2 loops) if present
    for i in range(profs.count):
        p = profs.item(i)
        try:
            if p.profileLoops and p.profileLoops.count == 2:
                return p
        except:
            pass
    return profs.item(0)

def _add_circle(sketch, cx_cm, cy_cm, r_cm):
    center = adsk.core.Point3D.create(cx_cm, cy_cm, 0)
    sketch.sketchCurves.sketchCircles.addByCenterRadius(center, r_cm)

def _ensure_design():
    app = adsk.core.Application.get()
    product = app.activeProduct
    design = adsk.fusion.Design.cast(product)
    if not design:
        raise RuntimeError("Please switch to the DESIGN workspace and try again.")
    return design

def _move_body(root_comp, body, dx_cm, dy_cm, dz_cm):
    move_feats = root_comp.features.moveFeatures
    coll = adsk.core.ObjectCollection.create()
    coll.add(body)
    xf = adsk.core.Matrix3D.create()
    xf.setToTranslation(adsk.core.Vector3D.create(dx_cm, dy_cm, dz_cm))
    inp = move_feats.createInput(coll, xf)
    move_feats.add(inp)

def run(context):
    app, ui = _get_ui_app_handlers()
    try:
        design = _ensure_design()
        root = design.rootComponent

        # Pick JSON
        dlg = ui.createFileDialog()
        dlg.title = "Select CADmium / Fusion360-ds JSON file"
        dlg.filter = "JSON files (*.json)"
        if dlg.showOpen() != adsk.core.DialogResults.DialogOK:
            return
        path = dlg.filename

        with open(path, 'r') as f:
            data = json.load(f)

        parts = data.get('parts', {})
        if not parts:
            ui.messageBox('No "parts" found in JSON.')
            return

        xy_plane = root.xYConstructionPlane

        for part_name, part in parts.items():
            csys = part.get('coordinate_system', {})
            euler_deg = csys.get('Euler Angles', [0.0, 0.0, 0.0])
            tx, ty, tz = csys.get('Translation Vector', [0.0, 0.0, 0.0])

            # Scale translations to cm
            tx_cm = tx * UNIT_MULT
            ty_cm = ty * UNIT_MULT
            tz_cm = tz * UNIT_MULT

            # Sketch on XY at z=0
            sketch = root.sketches.add(xy_plane)

            # Draw circles from JSON (apply sketch_scale + XY translation)
            drew_any = False
            sketch_dict = part.get('sketch', {})
            sk_scale = float(part.get('extrusion', {}).get('sketch_scale', 1.0))

            for face_val in sketch_dict.values():
                if not isinstance(face_val, dict):
                    continue
                for loop_val in face_val.items():
                    # loop_val can be ("loop_1", {...})
                    if not isinstance(loop_val, tuple) or not isinstance(loop_val[1], dict):
                        continue
                    for circ_val in loop_val[1].values():
                        if not isinstance(circ_val, dict):
                            continue
                        if 'Center' not in circ_val or 'Radius' not in circ_val:
                            continue
                        cx, cy = circ_val['Center']
                        r = circ_val['Radius']
                        cx_cm = (tx + sk_scale * cx) * UNIT_MULT
                        cy_cm = (ty + sk_scale * cy) * UNIT_MULT
                        r_cm  = (sk_scale * r) * UNIT_MULT
                        _add_circle(sketch, cx_cm, cy_cm, r_cm)
                        drew_any = True

            if not drew_any:
                ui.messageBox(f'No circles drawn for {part_name}; check JSON.')
                continue

            profile = _pick_profile_for_circles(sketch)
            if not profile:
                ui.messageBox(f'No valid profile for {part_name}.')
                continue

            # Extrusion
            ext = part.get('extrusion', {})
            depth_pos = float(ext.get('extrude_depth_towards_normal', 0.0)) * UNIT_MULT
            operation = _map_operation(ext.get('operation', 'NewBodyFeatureOperation'))

            # Flip if exactly 180° about X (common in dataset)
            ex = euler_deg[0] if len(euler_deg) == 3 else 0.0
            flip = abs((ex % 360.0) - 180.0) < 1e-6
            distance_cm = -depth_pos if flip else depth_pos

            extrudes = root.features.extrudeFeatures
            dist_input = adsk.core.ValueInput.createByReal(distance_cm)
            ext_input = extrudes.createInput(profile, operation)
            ext_input.setDistanceExtent(False, dist_input)
            extrude = extrudes.add(ext_input)

            # Name body (best effort)
            try:
                if extrude and extrude.bodies and extrude.bodies.count > 0:
                    body = extrude.bodies.item(0)
                    body.name = part_name
                    # Translate by tz along +Z after extrusion
                    if abs(tz_cm) > 1e-9:
                        _move_body(root, body, 0.0, 0.0, tz_cm)
            except:
                pass

        ui.messageBox('Replay complete.')

    except:
        if ui:
            ui.messageBox('Failed:\n{}'.format(traceback.format_exc()))

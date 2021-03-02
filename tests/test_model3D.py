import sys
import numpy as np
import pytest
from stardist.models import Config3D, StarDist3D
from stardist.matching import matching
from stardist.geometry import export_to_obj_file3D
from csbdeep.utils import normalize
from utils import circle_image, real_image3d, path_model3d


@pytest.mark.parametrize('n_rays, grid, n_channel, backbone, workers', [(73, (2, 2, 2), None, 'resnet', 1), (16, (1, 2, 4), 1, 'resnet', 2), (7, (2, 1, 1), 2, 'unet', 4)])
def test_model(tmpdir, n_rays, grid, n_channel, backbone, workers):
    img = circle_image(shape=(64, 80, 96))
    imgs = np.repeat(img[np.newaxis], 3, axis=0)

    if n_channel is not None:
        imgs = np.repeat(imgs[..., np.newaxis], n_channel, axis=-1)
    else:
        n_channel = 1

    X = imgs+.6*np.random.uniform(0, 1, imgs.shape)
    Y = (imgs if imgs.ndim == 4 else imgs[..., 0]).astype(int)

    conf = Config3D(
        backbone=backbone,
        rays=n_rays,
        grid=grid,
        n_channel_in=n_channel,
        use_gpu=False,
        train_epochs=1,
        train_steps_per_epoch=1,
        train_batch_size=2,
        train_loss_weights=(4, 1),
        train_patch_size=(48, 64, 32),
    )

    model = StarDist3D(conf, name='stardist', basedir=str(tmpdir))
    model.train(X, Y, validation_data=(X[:2], Y[:2]), workers=workers)
    ref = model.predict(X[0])
    res = model.predict(X[0], n_tiles=(
        (1, 2, 3) if X[0].ndim == 3 else (1, 2, 3, 1)))
    # assert all(np.allclose(u,v) for u,v in zip(ref,res))

    # ask to train only with foreground patches when there are none
    # include a constant label image that must trigger a warning
    conf.train_foreground_only = 1
    conf.train_steps_per_epoch = 1
    _X = X[:2]
    _Y = [np.zeros_like(Y[0]), np.ones_like(Y[1])]
    with pytest.warns(UserWarning):
        StarDist3D(conf, name='stardist', basedir=None).train(
            _X, _Y, validation_data=(X[-1:], Y[-1:]))


def test_load_and_predict(model3d):
    model = model3d
    img, mask = real_image3d()
    x = normalize(img, 1, 99.8)
    prob, dist = model.predict(x, n_tiles=(1, 2, 2))
    assert prob.shape == dist.shape[:3]
    assert model.config.n_rays == dist.shape[-1]
    labels, _ = model.predict_instances(x)
    assert labels.shape == img.shape[:3]
    stats = matching(mask, labels, thresh=0.5)
    assert (stats.fp, stats.tp, stats.fn) == (0, 30, 21)
    return model, labels


def test_load_and_predict_with_overlap(model3d):
    model = model3d
    img, mask = real_image3d()
    x = normalize(img, 1, 99.8)
    prob, dist = model.predict(x, n_tiles=(1, 2, 2))
    assert prob.shape == dist.shape[:3]
    assert model.config.n_rays == dist.shape[-1]
    labels, _ = model.predict_instances(x, nms_thresh=.5,
                                        overlap_label=-3)
    assert np.min(labels) == -3
    return model, labels


def test_predict_dense_sparse():
    model_path = path_model3d()
    model = StarDist3D(None, name=model_path.name,
                       basedir=str(model_path.parent))
    img, mask = real_image3d()
    x = normalize(img, 1, 99.8)
    labels1, res1 = model.predict_instances(x, n_tiles=(1, 2, 2), sparse = False)
    labels2, res2 = model.predict_instances(x, n_tiles=(1, 2, 2), sparse = True)
    assert np.allclose(labels1, labels2)
    assert all(np.allclose(res1[k], res2[k]) for k in set(res1.keys()).union(set(res2.keys())) )
    return labels2, labels2 


def test_load_and_export_TF():
    model_path = path_model3d()
    model = StarDist3D(None, name=model_path.name,
                       basedir=str(model_path.parent))
    model.export_TF(single_output=True, upsample_grid=False)
    model.export_TF(single_output=True, upsample_grid=True)


def test_optimize_thresholds(model3d):
    model = model3d
    img, mask = real_image3d()
    x = normalize(img, 1, 99.8)

    def _opt(model):
        return model.optimize_thresholds([x], [mask],
                                         nms_threshs=[.3, .5],
                                         iou_threshs=[.3, .5],
                                         optimize_kwargs=dict(tol=1e-1),
                                         save_to_json=False)

    t1 = _opt(model)
    # enforce implicit tiling
    model.config.train_batch_size = 1
    model.config.train_patch_size = tuple(s-1 for s in x.shape)
    t2 = _opt(model)
    assert all(np.allclose(t1[k], t2[k]) for k in t1.keys())
    return model


@pytest.mark.parametrize('grid',((1,1,1),(1,4,4)))
def test_stardistdata(grid):
    np.random.seed(42)
    from stardist.models import StarDistData3D
    from stardist import Rays_GoldenSpiral
    img, mask = real_image3d()
    s = StarDistData3D([img, img], [mask, mask], batch_size=1, grid=grid,
                       patch_size=(30, 40, 50), rays=Rays_GoldenSpiral(64), length=1)
    (img,), (prob, dist) = s[0]
    return (img,), (prob, dist), s


def test_stardistdata_sequence():
    from stardist.models import StarDistData3D
    from stardist import Rays_GoldenSpiral
    from csbdeep.utils.tf import keras_import
    Sequence = keras_import('utils','Sequence')

    x = np.zeros((10,32,48,64), np.uint16)
    x[:,10:-10,10:-10] = 1

    class MyData(Sequence):
        def __init__(self, dtype):
            self.dtype = dtype
        def __getitem__(self,n):
            return x[n]
        def __len__(self):
            return len(x)

    X = MyData(np.float32)
    Y = MyData(np.uint16)
    s = StarDistData3D(X,Y,
                       batch_size=1, patch_size=(32,32,32),
                       rays=Rays_GoldenSpiral(64), length=1)
    (img,), (prob, dist) = s[0]
    return (img,), (prob, dist), s


def test_mesh_export(model3d):
    model = model3d
    img, mask = real_image3d()
    x = normalize(img, 1, 99.8)
    labels, polys = model.predict_instances(x, nms_thresh=.5,
                                        overlap_label=-3)

    s = export_to_obj_file3D(polys,
                             "mesh.obj",scale = (.2,.1,.1))
    return s


def print_receptive_fields():
    backbone = "unet"
    for n_depth in (1,2,3):
        for grid in ((1,1,1),(2,2,2)):
            conf  = Config3D(backbone = backbone,
                             grid = grid,
                             unet_n_depth=n_depth)
            model = StarDist3D(conf, None, None)
            fov   = model._compute_receptive_field()
            print(f"backbone: {backbone} \t n_depth: {n_depth} \t grid {grid} -> fov: {fov}")
    backbone = "resnet"
    for grid in ((1,1,1),(2,2,2)):
        conf  = Config3D(backbone = backbone,
                         grid = grid)
        model = StarDist3D(conf, None, None)
        fov   = model._compute_receptive_field()
        print(f"backbone: {backbone} \t grid {grid} -> fov: {fov}")

        


    
def test_classes():
    from stardist.utils import mask_to_categorical
    
    def _parse(n_classes, classes):
        model = StarDist3D(Config3D(n_classes = n_classes), None, None)
        classes =  model._parse_classes_arg(classes, length = 1)
        return classes

    def _check_single_val(n_classes, classes=1):
        img, y_gt = real_image3d()
        
        labels_gt = set(np.unique(y_gt[y_gt>0]))
        p, cls_dict = mask_to_categorical(y_gt,
                                          n_classes=n_classes,
                                          classes = classes, return_cls_dict = True)
        assert p.shape == y_gt.shape+(n_classes+1,)
        assert tuple(cls_dict.keys()) == (classes,) and  set(cls_dict[classes]) == labels_gt
        assert set(np.where(np.count_nonzero(p, axis = (0,1,2)))[0]) == set({0,classes})
        return p, cls_dict
        
    assert _parse(None,"auto") is None
    assert _parse(1,   "auto") == (1,)

    p, cls_dict = _check_single_val(1,1)
    p, cls_dict = _check_single_val(2,1)
    p, cls_dict = _check_single_val(7,6)
    
    return p

def _test_model_multiclass(n_classes = 1, classes = "auto", n_channel = None, basedir = None):
    from skimage.measure import regionprops
    
    img, mask = real_image3d()
    img = normalize(img,1,99.8) 

    if n_channel is not None:
        img = np.repeat(img[..., np.newaxis], n_channel, axis=-1)
    else:
        n_channel = 1

    X, Y = [img, img, img], [mask, mask, mask]

    conf = Config3D(
        n_rays=32,
        grid=(2,1,2),
        n_channel_in=n_channel,
        n_classes = n_classes,
        use_gpu=False,
        train_epochs=1,
        train_steps_per_epoch=1,
        train_batch_size=1,
        train_loss_weights=(1.,.2) if n_classes is None else (1, .2, 1.),
        train_patch_size=(16,16,16),
    )

    if n_classes is not None and n_classes>1 and classes=="area":
        regs = regionprops(mask)
        areas = tuple(r.area for r in regs)
        inds = np.argsort(areas)
        ss = tuple(slice(n*len(regs)//n_classes,(n+1)*len(regs)//n_classes) for n in range(n_classes))
        classes = {}
        for i,s in enumerate(ss):
            for j in inds[s]:
                classes[regs[j].label] = i+1
        classes = (classes,)*len(X)

        
    model = StarDist3D(conf, name=None if basedir is None else "stardist", basedir=str(basedir))

    val_classes = {k:1 for k in set(mask[mask>0])}
    
    s = model.train(X, Y, classes = classes, epochs = 1, 
                validation_data=(X[:1], Y[:1]) if n_classes is None else (X[:1], Y[:1], (val_classes,))
                    )

    labels, res = model.predict_instances(img)
    return  model, X,Y, labels, res
    
@pytest.mark.parametrize('n_classes, classes, n_channel', [(None, "auto", 1), (1, "auto", 3), (3, (1,2,3),3)])
def test_model_multiclass(tmpdir, n_classes, classes, n_channel):
    return _test_model_multiclass(n_classes=n_classes, classes=classes,
                                  n_channel=n_channel, basedir = tmpdir)


# this test has to be at the end of the model
def test_load_and_export_TF(model3d):
    model = model3d
    assert any(g>1 for g in model.config.grid)
    # model.export_TF(single_output=False, upsample_grid=False)
    # model.export_TF(single_output=False, upsample_grid=True)
    model.export_TF(single_output=True, upsample_grid=False)
    model.export_TF(single_output=True, upsample_grid=True)


    
if __name__ == '__main__':
    # from conftest import _model3d
    # model, lbl = test_load_and_predict_with_overlap(_model3d())


    # test_classes()
    # res = _test_model_multiclass(n_classes = 2, classes="area", n_channel=1)

    # (img,), (prob, dist), s = test_stardistdata((1,1,1))

    test_model("foo", 73, (2, 2, 2), None, 'resnet')

#!/usr/bin/env python3
"""
Wrapper functions used to add functionality to various `~proplot.axes.Axes`
plotting methods. "Wrapped" plotting methods accept the additional keyword
arguments documented by the wrapper function. In a future version, these
features will be documented on the individual plotting methods.
"""
import sys
import numpy as np
import numpy.ma as ma
import functools
import matplotlib.axes as maxes
import matplotlib.container as mcontainer
import matplotlib.contour as mcontour
import matplotlib.ticker as mticker
import matplotlib.transforms as mtransforms
import matplotlib.patheffects as mpatheffects
import matplotlib.patches as mpatches
import matplotlib.colors as mcolors
import matplotlib.artist as martist
import matplotlib.legend as mlegend
import matplotlib.cm as mcm
from numbers import Number
from . import constructor
from . import colors as pcolors
from .config import rc
from .utils import edges, edges2d, units, to_xyz, to_rgb
from .internals import ic  # noqa: F401
from .internals import docstring, warnings, _not_none
try:
    from cartopy.crs import PlateCarree
except ModuleNotFoundError:
    PlateCarree = object

__all__ = [
    'add_errorbars',
    'bar_wrapper',
    'barh_wrapper',
    'boxplot_wrapper',
    'cmap_changer',
    'colorbar_wrapper',
    'cycle_changer',
    'default_crs',
    'default_latlon',
    'default_transform',
    'fill_between_wrapper',
    'fill_betweenx_wrapper',
    'hist_wrapper',
    'legend_wrapper',
    'scatter_wrapper',
    'standardize_1d',
    'standardize_2d',
    'text_wrapper',
    'violinplot_wrapper',
]


def _load_objects():
    """
    Delay loading expensive modules. We just want to detect if *input
    arrays* belong to these types -- and if this is the case, it means the
    module has already been imported! So, we only try loading these classes
    within autoformat calls. This saves >~500ms of import time.
    """
    global DataArray, DataFrame, Series, Index, ndarray
    ndarray = np.ndarray
    DataArray = getattr(sys.modules.get('xarray', None), 'DataArray', ndarray)
    DataFrame = getattr(sys.modules.get('pandas', None), 'DataFrame', ndarray)
    Series = getattr(sys.modules.get('pandas', None), 'Series', ndarray)
    Index = getattr(sys.modules.get('pandas', None), 'Index', ndarray)


_load_objects()

# Keywords for styling cmap overridden plots
# TODO: Deprecate this when #45 merged! Pcolor *already* accepts lw,
# linewidth, *and* linewidths!
STYLE_ARGS_TRANSLATE = {
    'contour': {
        'colors': 'colors',
        'linewidths': 'linewidths',
        'linestyles': 'linestyles',
    },
    'tricontour': {
        'colors': 'colors',
        'linewidths': 'linewidths',
        'linestyles': 'linestyles',
    },
    'pcolor': {
        'colors': 'edgecolors',
        'linewidths': 'linewidth',
        'linestyles': 'linestyle',
    },
    'pcolormesh': {
        'colors': 'edgecolors',
        'linewidths': 'linewidth',
        'linestyles': 'linestyle',
    },
    'tripcolor': {
        'colors': 'edgecolors',
        'linewidths': 'linewidth',
        'linestyles': 'linestyle',
    },
    'parametric': {
        'colors': 'color',
        'linewidths': 'linewidth',
        'linestyles': 'linestyle',
    },
    'hexbin': {
        'colors': 'edgecolors',
        'linewidths': 'linewidths',
        'linestyles': 'linestyles',
    },
}


def _is_number(data):
    """
    Test whether input is numeric array rather than datetime or strings.
    """
    return len(data) and np.issubdtype(_to_ndarray(data).dtype, np.number)


def _is_string(data):
    """
    Test whether input is array of strings.
    """
    return len(data) and isinstance(_to_ndarray(data).flat[0], str)


def _to_array(data):
    """
    Convert list of lists to array-like type.
    """
    _load_objects()
    if not isinstance(data, (ndarray, DataArray, DataFrame, Series, Index)):
        data = np.array(data)
    if not np.iterable(data):
        data = np.atleast_1d(data)
    return data


def _to_indexer(data):
    """
    Return indexible attribute of array-like type.
    """
    return getattr(data, 'iloc', data)


def _to_ndarray(data):
    """
    Convert arbitrary input to ndarray cleanly.
    """
    return np.asarray(getattr(data, 'values', data))


def default_latlon(self, func, *args, latlon=True, **kwargs):
    """
    Makes ``latlon=True`` the default for basemap plots.
    This means you no longer have to pass ``latlon=True`` if your data
    coordinates are longitude and latitude.

    Note
    ----
    This function wraps %(methods)s for `~proplot.axes.BasemapAxes`.
    """
    return func(self, *args, latlon=latlon, **kwargs)


def default_transform(self, func, *args, transform=None, **kwargs):
    """
    Makes ``transform=cartopy.crs.PlateCarree()`` the default
    for cartopy plots. This means you no longer have to
    pass ``transform=cartopy.crs.PlateCarree()`` if your data
    coordinates are longitude and latitude.

    Note
    ----
    This function wraps %(methods)s for `~proplot.axes.CartopyAxes`.
    """
    # Apply default transform
    # TODO: Do some cartopy methods reset backgroundpatch or outlinepatch?
    # Deleted comment reported this issue
    if transform is None:
        transform = PlateCarree()
    result = func(self, *args, transform=transform, **kwargs)
    return result


def default_crs(self, func, *args, crs=None, **kwargs):
    """
    Fixes the `~cartopy.mpl.geoaxes.GeoAxes.set_extent` bug associated with
    tight bounding boxes and makes ``crs=cartopy.crs.PlateCarree()`` the
    default for cartopy plots.

    Note
    ----
    This function wraps %(methods)s for `~proplot.axes.CartopyAxes`.
    """
    # Apply default crs
    name = func.__name__
    if crs is None:
        crs = PlateCarree()
    try:
        result = func(self, *args, crs=crs, **kwargs)
    except TypeError as err:  # duplicate keyword args, i.e. crs is positional
        if not args:
            raise err
        result = func(self, *args[:-1], crs=args[-1], **kwargs)
    # Fix extent, so axes tight bounding box gets correct box!
    # From this issue:
    # https://github.com/SciTools/cartopy/issues/1207#issuecomment-439975083
    if name == 'set_extent':
        clipped_path = self.outline_patch.orig_path.clip_to_bbox(self.viewLim)
        self.outline_patch._path = clipped_path
        self.background_patch._path = clipped_path
    return result


def _standard_label(data, axis=None, units=True):
    """
    Get data and label for pandas or xarray objects or their coordinates.
    """
    label = ''
    _load_objects()
    if isinstance(data, ndarray):
        if axis is not None and data.ndim > axis:
            data = np.arange(data.shape[axis])
    # Xarray with common NetCDF attribute names
    elif isinstance(data, DataArray):
        if axis is not None and data.ndim > axis:
            data = data.coords[data.dims[axis]]
        label = getattr(data, 'name', '') or ''
        for key in ('standard_name', 'long_name'):
            label = data.attrs.get(key, label)
        if units:
            units = data.attrs.get('units', '')
            if label and units:
                label = f'{label} ({units})'
            elif units:
                label = units
    # Pandas object with name attribute
    # if not label and isinstance(data, DataFrame) and data.columns.size == 1:
    elif isinstance(data, (DataFrame, Series, Index)):
        if axis == 0 and isinstance(data, (DataFrame, Series)):
            data = data.index
        elif axis == 1 and isinstance(data, DataFrame):
            data = data.columns
        elif axis is not None:
            data = np.arange(len(data))  # e.g. for Index
        # DataFrame has no native name attribute but user can add one:
        # https://github.com/pandas-dev/pandas/issues/447
        label = getattr(data, 'name', '') or ''
    return data, str(label).strip()


def standardize_1d(self, func, *args, **kwargs):
    """
    Interprets positional arguments for the "1d" plotting methods
    %(methods)s. This also optionally modifies the x axis label, y axis label,
    title, and axis ticks if a `~xarray.DataArray`, `~pandas.DataFrame`, or
    `~pandas.Series` is passed.

    Positional arguments are standardized as follows:

    * If a 2d array is passed, the corresponding plot command is called for
      each column of data (except for ``boxplot`` and ``violinplot``, in which
      case each column is interpreted as a distribution).
    * If *x* and *y* or *latitude* and *longitude* coordinates were not
      provided, and a `~pandas.DataFrame` or `~xarray.DataArray`, we
      try to infer them from the metadata. Otherwise,
      ``np.arange(0, data.shape[0])`` is used.

   See also
   --------
   cycle_changer
   """
    # Sanitize input
    # TODO: Add exceptions for methods other than 'hist'?
    name = func.__name__
    _load_objects()
    if not args:
        return func(self, *args, **kwargs)
    elif len(args) == 1:
        x = None
        y, *args = args
    elif len(args) in (2, 3, 4):
        x, y, *args = args  # same
    else:
        raise ValueError(f'Too many arguments passed to {name}. Max is 4.')
    vert = kwargs.get('vert', None)
    if vert is not None:
        orientation = ('vertical' if vert else 'horizontal')
    else:
        orientation = kwargs.get('orientation', 'vertical')

    # Iterate through list of ys that we assume are identical
    # Standardize based on the first y input
    if len(args) >= 1 and 'fill_between' in name:
        ys, args = (y, args[0]), args[1:]
    else:
        ys = (y,)
    ys = [_to_array(y) for y in ys]

    # Auto x coords
    y = ys[0]  # test the first y input
    if x is None:
        axis = 1 if (name in ('hist', 'boxplot', 'violinplot') or any(
            kwargs.get(s, None) for s in ('means', 'medians'))) else 0
        x, _ = _standard_label(y, axis=axis)
    x = _to_array(x)
    if x.ndim != 1:
        raise ValueError(
            f'x coordinates must be 1-dimensional, but got {x.ndim}.'
        )

    # Auto formatting
    xi = None  # index version of 'x'
    if not hasattr(self, 'projection'):
        # First handle string-type x-coordinates
        kw = {}
        xax = 'y' if orientation == 'horizontal' else 'x'
        yax = 'x' if xax == 'y' else 'y'
        if _is_string(x):
            xi = np.arange(len(x))
            kw[xax + 'locator'] = mticker.FixedLocator(xi)
            kw[xax + 'formatter'] = mticker.IndexFormatter(x)
            kw[xax + 'minorlocator'] = mticker.NullLocator()
            if name == 'boxplot':
                kwargs['labels'] = x
            elif name == 'violinplot':
                kwargs['positions'] = xi
        if name in ('boxplot', 'violinplot'):
            kwargs['positions'] = xi
        # Next handle labels if 'autoformat' is on
        if self.figure._auto_format:
            # Ylabel
            y, label = _standard_label(y)
            if label:
                # for histogram, this indicates x coordinate
                iaxis = xax if name in ('hist',) else yax
                kw[iaxis + 'label'] = label
            # Xlabel
            x, label = _standard_label(x)
            if label and name not in ('hist',):
                kw[xax + 'label'] = label
            if name != 'scatter' and len(x) > 1 and xi is None and x[1] < x[0]:
                kw[xax + 'reverse'] = True
        # Appply
        if kw:
            self.format(**kw)

    # Standardize args
    if xi is not None:
        x = xi
    if name in ('boxplot', 'violinplot'):
        ys = [_to_ndarray(yi) for yi in ys]  # store naked array

    # Basemap shift x coordiantes without shifting y, we fix this!
    if getattr(self, 'name', '') == 'basemap' and kwargs.get('latlon', None):
        ix, iys = x, []
        xmin, xmax = self.projection.lonmin, self.projection.lonmax
        for y in ys:
            # Ensure data is monotonic and falls within map bounds
            ix, iy = _enforce_bounds(*_standardize_latlon(x, y), xmin, xmax)
            iys.append(iy)
        x, ys = ix, iys

    # WARNING: For some functions, e.g. boxplot and violinplot, we *require*
    # cycle_changer is also applied so it can strip 'x' input.
    return func(self, x, *ys, *args, **kwargs)


def _enforce_bounds(x, y, xmin, xmax):
    """
    Ensure data for basemap plots is restricted between the minimum and
    maximum longitude of the projection. Input is the ``x`` and ``y``
    coordinates. The ``y`` coordinates are rolled along the rightmost axis.
    """
    if x.ndim != 1:
        return x, y
    # Roll in same direction if some points on right-edge extend
    # more than 360 above min longitude; *they* should be on left side
    lonroll = np.where(x > xmin + 360)[0]  # tuple of ids
    if lonroll.size:  # non-empty
        roll = x.size - lonroll.min()
        x = np.roll(x, roll)
        y = np.roll(y, roll, axis=-1)
        x[:roll] -= 360  # make monotonic

    # Set NaN where data not in range xmin, xmax. Must be done
    # for regional smaller projections or get weird side-effects due
    # to having valid data way outside of the map boundaries
    y = y.copy()
    if x.size - 1 == y.shape[-1]:  # test western/eastern grid cell edges
        y[..., (x[1:] < xmin) | (x[:-1] > xmax)] = np.nan
    elif x.size == y.shape[-1]:  # test the centers and pad by one for safety
        where = np.where((x < xmin) | (x > xmax))[0]
        y[..., where[1:-1]] = np.nan
    return x, y


def _interp_poles(y, Z):
    """
    Add data points on the poles as the average of highest latitude data.
    """
    # Get means
    with np.errstate(all='ignore'):
        p1 = Z[0, :].mean()  # pole 1, make sure is not 0D DataArray!
        p2 = Z[-1, :].mean()  # pole 2
    if hasattr(p1, 'item'):
        p1 = np.asscalar(p1)  # happens with DataArrays
    if hasattr(p2, 'item'):
        p2 = np.asscalar(p2)
    # Concatenate
    ps = (-90, 90) if (y[0] < y[-1]) else (90, -90)
    Z1 = np.repeat(p1, Z.shape[1])[None, :]
    Z2 = np.repeat(p2, Z.shape[1])[None, :]
    y = ma.concatenate((ps[:1], y, ps[1:]))
    Z = ma.concatenate((Z1, Z, Z2), axis=0)
    return y, Z


def _standardize_latlon(x, y):
    """
    Ensure longitudes are monotonic and make `~numpy.ndarray` copies so the
    contents can be modified. Ignores 2d coordinate arrays.
    """
    # Sanitization and bail if 2d
    if x.ndim == 1:
        x = ma.array(x)
    if y.ndim == 1:
        y = ma.array(y)
    if x.ndim != 1 or all(x < x[0]):  # skip monotonic backwards data
        return x, y
    # Enforce monotonic longitudes
    lon1 = x[0]
    while True:
        filter_ = (x < lon1)
        if filter_.sum() == 0:
            break
        x[filter_] += 360
    return x, y


def standardize_2d(self, func, *args, order='C', globe=False, **kwargs):
    """
    Interprets positional arguments for the "2d" plotting methods
    %(methods)s. This also optionally modifies the x axis label, y axis label,
    title, and axis ticks if a `~xarray.DataArray`, `~pandas.DataFrame`, or
    `~pandas.Series` is passed.

    Positional arguments are standardized as follows:

    * If *x* and *y* or *latitude* and *longitude* coordinates were not
      provided, and a `~pandas.DataFrame` or `~xarray.DataArray` is passed, we
      try to infer them from the metadata. Otherwise,
      ``np.arange(0, data.shape[0])`` and ``np.arange(0, data.shape[1])``
      are used.
    * For ``pcolor`` and ``pcolormesh``, coordinate *edges* are calculated
      if *centers* were provided. For all other methods, coordinate *centers*
      are calculated if *edges* were provided.

    For `~proplot.axes.CartopyAxes` and `~proplot.axes.BasemapAxes`, the
    `globe` keyword arg is added, suitable for plotting datasets with global
    coverage. Passing ``globe=True`` does the following:

    1. "Interpolates" input data to the North and South poles.
    2. Makes meridional coverage "circular", i.e. the last longitude coordinate
       equals the first longitude coordinate plus 360\N{DEGREE SIGN}.

    For `~proplot.axes.BasemapAxes`, 1d longitude vectors are also cycled to
    fit within the map edges. For example, if the projection central longitude
    is 90\N{DEGREE SIGN}, the data is shifted so that it spans
    -90\N{DEGREE SIGN} to 270\N{DEGREE SIGN}.

    See also
    --------
    cmap_changer
    """
    # Sanitize input
    name = func.__name__
    _load_objects()
    if not args:
        return func(self, *args, **kwargs)
    elif len(args) > 4:
        raise ValueError(f'Too many arguments passed to {name}. Max is 4.')
    x, y = None, None
    if len(args) > 2:
        x, y, *args = args

    # Ensure DataArray, DataFrame or ndarray
    Zs = []
    for Z in args:
        Z = _to_array(Z)
        if Z.ndim != 2:
            raise ValueError(f'Z must be 2-dimensional, got shape {Z.shape}.')
        Zs.append(Z)
    if not all(Zs[0].shape == Z.shape for Z in Zs):
        raise ValueError(
            f'Zs must be same shape, got shapes {[Z.shape for Z in Zs]}.'
        )

    # Retrieve coordinates
    if x is None and y is None:
        Z = Zs[0]
        if order == 'C':  # TODO: check order stuff works
            idx, idy = 1, 0
        else:
            idx, idy = 0, 1
        if isinstance(Z, ndarray):
            x = np.arange(Z.shape[idx])
            y = np.arange(Z.shape[idy])
        elif isinstance(Z, DataArray):  # DataArray
            x = Z.coords[Z.dims[idx]]
            y = Z.coords[Z.dims[idy]]
        else:  # DataFrame; never Series or Index because these are 1d
            x = Z.index
            y = Z.columns

    # Check coordinates
    x, y = _to_array(x), _to_array(y)
    if x.ndim != y.ndim:
        raise ValueError(
            f'x coordinates are {x.ndim}-dimensional, '
            f'but y coordinates are {y.ndim}-dimensional.'
        )
    for s, array in zip(('x', 'y'), (x, y)):
        if array.ndim not in (1, 2):
            raise ValueError(
                f'{s} coordinates are {array.ndim}-dimensional, '
                f'but must be 1 or 2-dimensional.'
            )

    # Auto formatting
    kw = {}
    xi, yi = None, None
    if not hasattr(self, 'projection'):
        # First handle string-type x and y-coordinates
        if _is_string(x):
            xi = np.arange(len(x))
            kw['xlocator'] = mticker.FixedLocator(xi)
            kw['xformatter'] = mticker.IndexFormatter(x)
            kw['xminorlocator'] = mticker.NullLocator()
        if _is_string(x):
            yi = np.arange(len(y))
            kw['ylocator'] = mticker.FixedLocator(yi)
            kw['yformatter'] = mticker.IndexFormatter(y)
            kw['yminorlocator'] = mticker.NullLocator()
        # Handle labels if 'autoformat' is on
        if self.figure._auto_format:
            for key, xy in zip(('xlabel', 'ylabel'), (x, y)):
                _, label = _standard_label(xy)
                if label:
                    kw[key] = label
                if len(xy) > 1 and all(isinstance(xy, Number)
                                       for xy in xy[:2]) and xy[1] < xy[0]:
                    kw[key[0] + 'reverse'] = True
    if xi is not None:
        x = xi
    if yi is not None:
        y = yi
    # Handle figure titles
    if self.figure._auto_format:
        _, colorbar_label = _standard_label(Zs[0], units=True)
        _, title = _standard_label(Zs[0], units=False)
        if title:
            kw['title'] = title
        if kw:
            self.format(**kw)

    # Enforce edges
    if name in ('pcolor', 'pcolormesh'):
        # Get centers or raise error. If 2d, don't raise error, but don't fix
        # either, because matplotlib pcolor just trims last column and row.
        xlen, ylen = x.shape[-1], y.shape[0]
        for Z in Zs:
            if Z.ndim != 2:
                raise ValueError(
                    f'Input arrays must be 2d, instead got shape {Z.shape}.'
                )
            elif Z.shape[1] == xlen and Z.shape[0] == ylen:
                if all(
                    z.ndim == 1 and z.size > 1
                    and _is_number(z) for z in (x, y)
                ):
                    x = edges(x)
                    y = edges(y)
                else:
                    if (
                        x.ndim == 2 and x.shape[0] > 1 and x.shape[1] > 1
                        and _is_number(x)
                    ):
                        x = edges2d(x)
                    if (
                        y.ndim == 2 and y.shape[0] > 1 and y.shape[1] > 1
                        and _is_number(y)
                    ):
                        y = edges2d(y)
            elif Z.shape[1] != xlen - 1 or Z.shape[0] != ylen - 1:
                raise ValueError(
                    f'Input shapes x {x.shape} and y {y.shape} must match '
                    f'Z centers {Z.shape} or '
                    f'Z borders {tuple(i+1 for i in Z.shape)}.'
                )
        # Optionally re-order
        # TODO: Double check this
        if order == 'F':
            x, y = x.T, y.T  # in case they are 2-dimensional
            Zs = (Z.T for Z in Zs)
        elif order != 'C':
            raise ValueError(
                f'Invalid order {order!r}. Choose from '
                '"C" (row-major, default) and "F" (column-major).'
            )

    # Enforce centers
    else:
        # Get centers given edges. If 2d, don't raise error, let matplotlib
        # raise error down the line.
        xlen, ylen = x.shape[-1], y.shape[0]
        for Z in Zs:
            if Z.ndim != 2:
                raise ValueError(
                    f'Input arrays must be 2d, instead got shape {Z.shape}.'
                )
            elif Z.shape[1] == xlen - 1 and Z.shape[0] == ylen - 1:
                if all(
                    z.ndim == 1 and z.size > 1
                    and _is_number(z) for z in (x, y)
                ):
                    x = (x[1:] + x[:-1]) / 2
                    y = (y[1:] + y[:-1]) / 2
                else:
                    if (
                        x.ndim == 2 and x.shape[0] > 1 and x.shape[1] > 1
                        and _is_number(x)
                    ):
                        x = 0.25 * (
                            x[:-1, :-1] + x[:-1, 1:] + x[1:, :-1] + x[1:, 1:]
                        )
                    if (
                        y.ndim == 2 and y.shape[0] > 1 and y.shape[1] > 1
                        and _is_number(y)
                    ):
                        y = 0.25 * (
                            y[:-1, :-1] + y[:-1, 1:] + y[1:, :-1] + y[1:, 1:]
                        )
            elif Z.shape[1] != xlen or Z.shape[0] != ylen:
                raise ValueError(
                    f'Input shapes x {x.shape} and y {y.shape} '
                    f'must match Z centers {Z.shape} '
                    f'or Z borders {tuple(i+1 for i in Z.shape)}.'
                )
        # Optionally re-order
        # TODO: Double check this
        if order == 'F':
            x, y = x.T, y.T  # in case they are 2-dimensional
            Zs = (Z.T for Z in Zs)
        elif order != 'C':
            raise ValueError(
                f'Invalid order {order!r}. Choose from '
                '"C" (row-major, default) and "F" (column-major).'
            )

    # Cartopy projection axes
    if (
        getattr(self, 'name', '') == 'cartopy'
        and isinstance(kwargs.get('transform', None), PlateCarree)
    ):
        x, y = _standardize_latlon(x, y)
        ix, iZs = x, []
        for Z in Zs:
            if globe and x.ndim == 1 and y.ndim == 1:
                # Fix holes over poles by *interpolating* there
                y, Z = _interp_poles(y, Z)

                # Fix seams by ensuring circular coverage. Unlike basemap,
                # cartopy can plot across map edges.
                if (x[0] % 360) != ((x[-1] + 360) % 360):
                    ix = ma.concatenate((x, [x[0] + 360]))
                    Z = ma.concatenate((Z, Z[:, :1]), axis=1)
            iZs.append(Z)
        x, Zs = ix, iZs

    # Basemap projection axes
    elif getattr(self, 'name', '') == 'basemap' and kwargs.get('latlon', None):
        # Fix grid
        xmin, xmax = self.projection.lonmin, self.projection.lonmax
        x, y = _standardize_latlon(x, y)
        ix, iZs = x, []
        for Z in Zs:
            # Ensure data is within map bounds
            ix, Z = _enforce_bounds(x, Z, xmin, xmax)

            # Globe coverage fixes
            if globe and ix.ndim == 1 and y.ndim == 1:
                # Fix holes over poles by interpolating there (equivalent to
                # simple mean of highest/lowest latitude points)
                y, Z = _interp_poles(y, Z)

                # Fix seams at map boundary; 3 scenarios here:
                # Have edges (e.g. for pcolor), and they fit perfectly against
                # basemap seams. Does not augment size.
                if ix[0] == xmin and ix.size - 1 == Z.shape[1]:
                    pass  # do nothing
                # Have edges (e.g. for pcolor), and the projection edge is
                # in-between grid cell boundaries. Augments size by 1.
                elif ix.size - 1 == Z.shape[1]:  # just add grid cell
                    ix = ma.append(xmin, ix)
                    ix[-1] = xmin + 360
                    Z = ma.concatenate((Z[:, -1:], Z), axis=1)
                # Have centers (e.g. for contourf), and we need to interpolate
                # to left/right edges of the map boundary. Augments size by 2.
                elif ix.size == Z.shape[1]:
                    xi = np.array([ix[-1], ix[0] + 360])  # x
                    if xi[0] != xi[1]:
                        Zq = ma.concatenate((Z[:, -1:], Z[:, :1]), axis=1)
                        xq = xmin + 360
                        Zq = (
                            Zq[:, :1] * (xi[1] - xq) + Zq[:, 1:] * (xq - xi[0])
                        ) / (xi[1] - xi[0])
                        ix = ma.concatenate(([xmin], ix, [xmin + 360]))
                        Z = ma.concatenate((Zq, Z, Zq), axis=1)
                else:
                    raise ValueError(
                        'Unexpected shape of longitude/latitude/data arrays.'
                    )
            iZs.append(Z)
        x, Zs = ix, iZs

        # Convert to projection coordinates
        if x.ndim == 1 and y.ndim == 1:
            x, y = np.meshgrid(x, y)
        x, y = self.projection(x, y)
        kwargs['latlon'] = False

    # Finally return result
    # WARNING: Must apply default colorbar label *here* in case metadata
    # was stripped by globe=True.
    colorbar_kw = kwargs.pop('colorbar_kw', None) or {}
    colorbar_kw.setdefault('label', colorbar_label)
    return func(self, x, y, *Zs, colorbar_kw=colorbar_kw, **kwargs)


def _errorbar_values(data, idata, bardata=None, barrange=None, barstd=False):
    """
    Return values that can be passed to the `~matplotlib.axes.Axes.errorbar`
    `xerr` and `yerr` keyword args.
    """
    if bardata is not None:
        err = np.array(bardata)
        if err.ndim == 1:
            err = err[:, None]
        if err.ndim != 2 or err.shape[0] != 2 \
                or err.shape[1] != idata.shape[-1]:
            raise ValueError(
                f'bardata must have shape (2, {idata.shape[-1]}), '
                f'but got {err.shape}.'
            )
    elif barstd:
        err = np.array(idata) + \
            np.std(data, axis=0)[None, :] * np.array(barrange)[:, None]
    else:
        err = np.percentile(data, barrange, axis=0)
    err = err - np.array(idata)
    err[0, :] *= -1  # array now represents error bar sizes
    return err


def add_errorbars(
    self, func, *args,
    medians=False, means=False,
    boxes=None, bars=None,
    boxdata=None, bardata=None,
    boxstd=False, barstd=False,
    boxmarker=True, boxmarkercolor='white',
    boxrange=(25, 75), barrange=(5, 95), boxcolor=None, barcolor=None,
    boxlw=None, barlw=None, capsize=None,
    boxzorder=3, barzorder=3,
    **kwargs
):
    """
    Adds support for drawing error bars on-the-fly.
    Includes options for interpreting columns of data as *samples*,
    representing the mean or median of each sample with lines, points, or
    bars, and drawing error bars representing percentile ranges or standard
    deviation multiples for each sample. Also supports specifying error
    bar data explicitly.

    Note
    ----
    This function wraps the 1d plotting methods: %(methods)s.

    Parameters
    ----------
    *args
        The input data.
    bars : bool, optional
        Toggles *thin* error bars with optional "whiskers" (i.e. caps). Default
        is ``True`` when `means` is ``True``, `medians` is ``True``, or
        `bardata` is not ``None``.
    boxes : bool, optional
        Toggles *thick* boxplot-like error bars with a marker inside
        representing the mean or median. Default is ``True`` when `means` is
        ``True``, `medians` is ``True``, or `boxdata` is not ``None``.
    means : bool, optional
        Whether to plot the means of each column in the input data.
    medians : bool, optional
        Whether to plot the medians of each column in the input data.
    bardata, boxdata : 2xN ndarray, optional
        Arrays that manually specify the thin and thick error bar coordinates.
        The first row contains lower bounds, and the second row contains
        upper bounds. Columns correspond to points in the dataset.
    barstd, boxstd : bool, optional
        Whether `barrange` and `boxrange` refer to multiples of the standard
        deviation, or percentile ranges. Default is ``False``.
    barrange : (float, float), optional
        Percentile ranges or standard deviation multiples for drawing thin
        error bars. The defaults are ``(-3, 3)`` (i.e. +/-3 standard
        deviations) when `barstd` is ``True``, and ``(0, 100)`` (i.e. the full
        data range) when `barstd` is ``False``.
    boxrange : (float, float), optional
        Percentile ranges or standard deviation multiples for drawing thick
        error bars. The defaults are ``(-1, 1)`` (i.e. +/-1 standard deviation)
        when `boxstd` is ``True``, and ``(25, 75)`` (i.e. the interquartile
        range) when `boxstd` is ``False``.
    barcolor, boxcolor : color-spec, optional
        Colors for the thick and thin error bars. Default is ``'k'``.
    barlw, boxlw : float, optional
        Line widths for the thin and thick error bars, in points. Default
        `barlw` is ``0.7`` and default `boxlw` is ``4 * barlw``.
    boxmarker : bool, optional
        Whether to draw a small marker in the middle of the box denoting
        the mean or median position. Ignored if `boxes` is ``False``.
        Default is ``True``.
    boxmarkercolor : color-spec, optional
        Color for the `boxmarker` marker. Default is ``'w'``.
    capsize : float, optional
        The cap size for thin error bars, in points.
    barzorder, boxzorder : float, optional
        The "zorder" for the thin and thick error bars.
    lw, linewidth : float, optional
        If passed, this is used for the default `barlw`.
    edgecolor : float, optional
        If passed, this is used for the default `barcolor` and `boxcolor`.
    """
    name = func.__name__
    x, y, *args = args
    # Sensible defaults
    if boxdata is not None:
        bars = _not_none(bars, True)
    if bardata is not None:
        boxes = _not_none(boxes, True)
    if boxdata is not None or bardata is not None:
        # e.g. if boxdata passed but bardata not passed, use bars=False
        bars = _not_none(bars, False)
        boxes = _not_none(boxes, False)

    # Get means or medians for plotting
    iy = y
    if (means or medians):
        bars = _not_none(bars, True)
        boxes = _not_none(boxes, True)
        if y.ndim != 2:
            raise ValueError(
                f'Need 2d data array for means=True or medians=True, '
                f'got {y.ndim}d array.'
            )
        if means:
            iy = np.mean(y, axis=0)
        elif medians:
            iy = np.percentile(y, 50, axis=0)

    # Call function, accounting for different signatures of plot and violinplot
    get = kwargs.pop if name == 'violinplot' else kwargs.get
    lw = _not_none(get('lw', None), get('linewidth', None), 0.7)
    get = kwargs.pop if name != 'bar' else kwargs.get
    edgecolor = _not_none(get('edgecolor', None), 'k')
    if name == 'violinplot':
        xy = (x, y)  # full data
    else:
        xy = (x, iy)  # just the stats
    obj = func(self, *xy, *args, **kwargs)
    if not boxes and not bars:
        return obj

    # Account for horizontal bar plots
    if 'vert' in kwargs:
        orientation = 'vertical' if kwargs['vert'] else 'horizontal'
    else:
        orientation = kwargs.get('orientation', 'vertical')
    if orientation == 'horizontal':
        axis = 'x'  # xerr
        xy = (iy, x)
    else:
        axis = 'y'  # yerr
        xy = (x, iy)

    # Defaults settings
    barlw = _not_none(barlw, lw)
    boxlw = _not_none(boxlw, 4 * barlw)
    capsize = _not_none(capsize, 3)
    barcolor = _not_none(barcolor, edgecolor)
    boxcolor = _not_none(boxcolor, edgecolor)

    # Draw boxes and bars
    if boxes:
        default = (-1, 1) if barstd else (25, 75)
        boxrange = _not_none(boxrange, default)
        err = _errorbar_values(y, iy, boxdata, boxrange, boxstd)
        if boxmarker:
            self.scatter(
                *xy, marker='o', color=boxmarkercolor,
                s=boxlw, zorder=5
            )
        self.errorbar(*xy, **{
            axis + 'err': err, 'capsize': 0, 'zorder': boxzorder,
            'color': boxcolor, 'linestyle': 'none', 'linewidth': boxlw
        })
    if bars:  # now impossible to make thin bar width different from cap width!
        default = (-3, 3) if barstd else (0, 100)
        barrange = _not_none(barrange, default)
        err = _errorbar_values(y, iy, bardata, barrange, barstd)
        self.errorbar(*xy, **{
            axis + 'err': err, 'capsize': capsize, 'zorder': barzorder,
            'color': barcolor, 'linewidth': barlw, 'linestyle': 'none',
            'markeredgecolor': barcolor, 'markeredgewidth': barlw
        })
    return obj


def _plot_wrapper_deprecated(
    self, func, *args, cmap=None, values=None, **kwargs
):
    """
    Calls `~proplot.axes.Axes.parametric` in certain cases, but this behavior
    is now deprecated.
    """
    if len(args) > 3:  # e.g. with fmt string
        raise ValueError(f'Expected 1-3 positional args, got {len(args)}.')
    if cmap is None:
        return func(self, *args, values=values, **kwargs)
    else:
        warnings._warn_proplot(
            'Drawing "parametric" plots with ax.plot(..., cmap=cmap, values='
            'values) is deprecated and will be removed in a future version. '
            'Please use ax.parametric(..., cmap=cmap, values=values) instead.'
        )
        return self.parametric(*args, cmap=cmap, values=values, **kwargs)


def scatter_wrapper(
    self, func, *args,
    s=None, size=None, markersize=None,
    c=None, color=None, markercolor=None,
    smin=None, smax=None,
    cmap=None, cmap_kw=None, vmin=None, vmax=None, norm=None, norm_kw=None,
    lw=None, linewidth=None, linewidths=None,
    markeredgewidth=None, markeredgewidths=None,
    edgecolor=None, edgecolors=None,
    markeredgecolor=None, markeredgecolors=None,
    **kwargs
):
    """
    Adds keyword arguments to `~matplotlib.axes.Axes.scatter` that are more
    consistent with the `~matplotlib.axes.Axes.plot` keyword arguments, and
    interpret the `cmap` and `norm` keyword arguments with
    `~proplot.constructor.Colormap` and `~proplot.constructor.Norm` like
    in `cmap_changer`.

    Note
    ----
    This function wraps %(methods)s.

    Parameters
    ----------
    s, size, markersize : float or list of float, optional
        The marker size(s). The units are scaled by `smin` and `smax`.
    smin, smax : float, optional
        The minimum and maximum marker size in units points ** 2 used to
        scale the `s` array. If not provided, the marker sizes are equivalent
        to the values in the `s` array.
    c, color, markercolor : color-spec or list thereof, or array, optional
        The marker fill color(s). If this is an array of scalar values, the
        colors will be generated by passing the values through the `norm`
        normalizer and drawing from the `cmap` colormap.
    cmap : colormap-spec, optional
        The colormap specifer, passed to the `~proplot.constructor.Colormap`
        constructor.
    cmap_kw : dict-like, optional
        Passed to `~proplot.constructor.Colormap`.
    vmin, vmax : float, optional
        Used to generate a `norm` for scaling the `c` array. These are the
        values corresponding to the leftmost and rightmost colors in the
        colormap. Defaults are the minimum and maximum values of the `c` array.
    norm : normalizer spec, optional
        The colormap normalizer, passed to the `~proplot.constructor.Norm`
        constructor.
    norm_kw : dict, optional
        Passed to `~proplot.constructor.Norm`.
    lw, linewidth, linewidths, markeredgewidth, markeredgewidths : \
float or list thereof, optional
        The marker edge width.
    edgecolors, markeredgecolor, markeredgecolors : \
color-spec or list thereof, optional
        The marker edge color.

    Other parameters
    ----------------
    **kwargs
        Passed to `~matplotlib.axes.Axes.scatter`.
    """
    # Manage input arguments
    # NOTE: Parse 1d must come before this
    nargs = len(args)
    if len(args) > 4:
        raise ValueError(f'Expected 1-4 positional args, got {nargs}.')
    args = list(args)
    if len(args) == 4:
        c = args.pop(1)
    if len(args) == 3:
        s = args.pop(0)

    # Format cmap and norm
    cmap_kw = cmap_kw or {}
    norm_kw = norm_kw or {}
    if cmap is not None:
        cmap = constructor.Colormap(cmap, **cmap_kw)
    if norm is not None:
        norm = constructor.Norm(norm, **norm_kw)

    # Apply some aliases for keyword arguments
    c = _not_none(c=c, color=color, markercolor=markercolor)
    s = _not_none(s=s, size=size, markersize=markersize)
    lw = _not_none(
        lw=lw, linewidth=linewidth, linewidths=linewidths,
        markeredgewidth=markeredgewidth, markeredgewidths=markeredgewidths,
    )
    ec = _not_none(
        edgecolor=edgecolor, edgecolors=edgecolors,
        markeredgecolor=markeredgecolor, markeredgecolors=markeredgecolors,
    )

    # Scale s array
    if np.iterable(s) and (smin is not None or smax is not None):
        smin_true, smax_true = min(s), max(s)
        if smin is None:
            smin = smin_true
        if smax is None:
            smax = smax_true
        s = (
            smin + (smax - smin)
            * (np.array(s) - smin_true) / (smax_true - smin_true)
        )
    return func(
        self, *args, c=c, s=s,
        cmap=cmap, vmin=vmin, vmax=vmax,
        norm=norm, linewidths=lw, edgecolors=ec,
        **kwargs
    )


_area_docstring = """
Supports overlaying and stacking successive columns of data, and permits
using different colors for "negative" and "positive" regions.

Note
----
This function wraps `~matplotlib.axes.Axes.fill_between{suffix}` and
`~proplot.axes.Axes.area{suffix}`.

Parameters
----------
*args : ({y}1,), ({x}, {y}1), or ({x}, {y}1, {y}2)
    The *{x}* and *{y}* coordinates. If `{x}` is not provided, it will be
    inferred from `{y}1`. If `{y}1` and `{y}2` are provided, this function
    will shade between respective columns of the arrays. The default value
    for `{y}2` is ``0``.
stacked : bool, optional
    Whether to "stack" successive columns of the `{y}1` array. If this is
    ``True`` and `{y}2` was provided, it will be ignored.
negpos : bool, optional
    Whether to shade where `{y}1` is greater than `{y}2` with the color
    `poscolor`, and where `{y}1` is less than `{y}2` with the color
    `negcolor`. For example, to shade positive values red and negative values
    blue, use ``ax.fill_between{suffix}({x}, {y}, negpos=True)``.
negcolor, poscolor : color-spec, optional
    Colors to use for the negative and positive values. Ignored if `negpos`
    is ``False``.
where : ndarray, optional
    Boolean ndarray mask for points you want to shade. See `this example \
<https://matplotlib.org/3.1.0/gallery/pyplots/whats_new_98_4_fill_between.html#sphx-glr-gallery-pyplots-whats-new-98-4-fill-between-py>`__.

Other parameters
----------------
**kwargs
    Passed to `~matplotlib.axes.Axes.fill_between`.
"""
docstring.snippets['axes.fill_between'] = _area_docstring.format(
    x='x', y='y', suffix='',
)
docstring.snippets['axes.fill_betweenx'] = _area_docstring.format(
    x='y', y='x', suffix='x',
)


def _fill_between_apply(
    self, func, *args,
    negcolor='blue', poscolor='red', negpos=False,
    **kwargs
):
    """
    Helper function that powers `fill_between` and `fill_betweenx`.
    """
    # Parse input arguments as follows:
    # * Permit using 'x', 'y1', and 'y2' or 'y', 'x1', and 'x2' as
    #   keyword arguments.
    # * If negpos=True, instead of using fill_between(x, y1, y2=0) as default,
    #   make the default fill_between(x, y1=0, y2).
    x = 'y' if 'x' in func.__name__ else 'x'
    y = 'x' if x == 'y' else 'y'
    if x in kwargs:
        args = (kwargs.pop(x), *args)
    for y in (y + '1', y + '2'):
        if y in kwargs:
            args = (*args, kwargs.pop(y))
    if len(args) == 1:
        args = (np.arange(len(args[0])), *args)
    if len(args) == 2:
        args = (*args, 0)
    elif len(args) == 3:
        if kwargs.get('stacked', False):
            warnings._warn_proplot(
                f'{func.__name__} cannot have three positional arguments '
                f'with negpos=True. Ignoring third argument.'
            )
    else:
        raise ValueError(f'Expected 2-3 positional args, got {len(args)}.')

    # Draw patches
    x, y1, y2 = args
    if negpos:
        # Get zero points
        objs = []
        kwargs.setdefault('interpolate', True)
        where = kwargs.pop('where', None)
        if where is not None:
            warnings._warn_proplot(
                f'{func.__name__} cannot have "where" argument '
                f'with negpos=True. Ignoring where={where!r}.'
            )

        # Plot negative and positive patches
        for i in range(2):
            kw = kwargs.copy()
            kw.setdefault('color', negcolor if i == 0 else poscolor)
            where = y1 < y2 if i == 0 else y1 >= y2
            obj = func(self, x, y1, y2, where=where, **kw)
            objs.append(obj)
        return tuple(objs)

    else:
        # Plot basic patches
        return func(self, x, y1, y2, **kwargs)


@docstring.add_snippets
def fill_between_wrapper(self, func, *args, **kwargs):
    """
    %(axes.fill_between)s
    """
    return _fill_between_apply(self, func, *args, **kwargs)


@docstring.add_snippets
def fill_betweenx_wrapper(self, func, *args, **kwargs):
    """
    %(axes.fill_betweenx)s
    """
    return _fill_between_apply(self, func, *args, **kwargs)


def hist_wrapper(self, func, x, bins=None, **kwargs):
    """
    Enforces that all arguments after `bins` are keyword-only and sets the

    Note
    ----
    This function wraps %(methods)s.
    """
    kwargs.setdefault('linewidth', 0)
    return func(self, x, bins=bins, **kwargs)


_bar_docstring = """
Supports grouping and stacking successive columns of data, and changes
the default bar style.

Note
----
This function wraps `~matplotlib.axes.Axes.bar{suffix}`.

Parameters
----------
{x}, {height}, {width}, {bottom} : float or list of float, optional
    The dimensions of the bars. If the *{x}* coordinates are not provided,
    they are set to ``np.arange(0, len(height))``.
orientation : {{'vertical', 'horizontal'}}, optional
    The orientation of the bars.
vert : bool, optional
    Alternative to the `orientation` keyword arg. If ``False``, horizontal
    bars are drawn. This is for consistency with
    `~matplotlib.axes.Axes.boxplot` and `~matplotlib.axes.Axes.violinplot`.
stacked : bool, optional
    Whether to stack columns of input data, or plot the bars side-by-side.
edgecolor : color-spec, optional
    The edge color for the bar patches.
lw, linewidth : float, optional
    The edge width for the bar patches.

Other parameters
----------------
**kwargs
    Passed to `~matplotlib.axes.Axes.bar{suffix}`.
"""
docstring.snippets['axes.bar'] = _bar_docstring.format(
    x='x', height='height', width='width', bottom='bottom', suffix='',
)
docstring.snippets['axes.barh'] = _bar_docstring.format(
    x='y', height='width', width='height', bottom='left', suffix='h',
)


@docstring.add_snippets
def bar_wrapper(
    self, func, x=None, height=None, width=0.8, bottom=None, *, left=None,
    vert=None, orientation='vertical', stacked=False,
    lw=None, linewidth=0.7, edgecolor='k',
    **kwargs
):
    """
    %(axes.bar)s
    """
    if vert is not None:
        orientation = ('vertical' if vert else 'horizontal')
    if orientation == 'horizontal':
        x, bottom = bottom, x
        width, height = height, width

    # Parse args
    # TODO: Stacked feature is implemented in `cycle_changer`, but makes more
    # sense do document here; figure out way to move it here?
    if left is not None:
        warnings._warn_proplot(
            f'The "left" keyword with bar() is deprecated. Use "x" instead.'
        )
        x = left
    if x is None and height is None:
        raise ValueError(
            f'bar() requires at least 1 positional argument, got 0.'
        )
    elif height is None:
        x, height = None, x

    # Call func
    # TODO: This *must* also be wrapped by cycle_changer, which ultimately
    # permutes back the x/bottom args for horizontal bars! Need to clean up.
    lw = _not_none(lw=lw, linewidth=linewidth)
    return func(
        self, x, height, width=width, bottom=bottom,
        linewidth=lw, edgecolor=edgecolor,
        stacked=stacked, orientation=orientation,
        **kwargs
    )


@docstring.add_snippets  # noqa: U100
def barh_wrapper(
    self, func, y=None, width=None, height=0.8, left=None, **kwargs
):
    """
    %(axes.barh)s
    """
    # Converts y-->bottom, left-->x, width-->height, height-->width.
    # Convert back to (x, bottom, width, height) so we can pass stuff
    # through cycle_changer.
    # NOTE: You *must* do juggling of barh keyword order --> bar keyword order
    # --> barh keyword order, because horizontal hist passes arguments to bar
    # directly and will not use a 'barh' method with overridden argument order!
    kwargs.setdefault('orientation', 'horizontal')
    if y is None and width is None:
        raise ValueError(
            f'barh() requires at least 1 positional argument, got 0.'
        )
    return self.bar(x=left, height=height, width=width, bottom=y, **kwargs)


def boxplot_wrapper(
    self, func, *args,
    color='k', fill=True, fillcolor=None, fillalpha=0.7,
    lw=None, linewidth=0.7, orientation=None,
    marker=None, markersize=None,
    boxcolor=None, boxlw=None,
    capcolor=None, caplw=None,
    meancolor=None, meanlw=None,
    mediancolor=None, medianlw=None,
    whiskercolor=None, whiskerlw=None,
    fliercolor=None, flierlw=None,
    **kwargs
):
    """
    Adds convenient keyword arguments and changes the default boxplot style.

    Note
    ----
    This function wraps %(methods)s.

    Parameters
    ----------
    *args : 1d or 2d ndarray
        The data array.
    color : color-spec, optional
        The color of all objects.
    fill : bool, optional
        Whether to fill the box with a color.
    fillcolor : color-spec, optional
        The fill color for the boxes. Default is the next color cycler color.
    fillalpha : float, optional
        The opacity of the boxes. Default is ``1``.
    lw, linewidth : float, optional
        The linewidth of all objects.
    orientation : {None, 'horizontal', 'vertical'}, optional
        Alternative to the native `vert` keyword arg. Controls orientation.
    marker : marker-spec, optional
        Marker style for the 'fliers', i.e. outliers.
    markersize : float, optional
        Marker size for the 'fliers', i.e. outliers.
    boxcolor, capcolor, meancolor, mediancolor, whiskercolor : \
color-spec, optional
        The color of various boxplot components. These are shorthands so you
        don't have to pass e.g. a ``boxprops`` dictionary.
    boxlw, caplw, meanlw, medianlw, whiskerlw : float, optional
        The line width of various boxplot components. These are shorthands so
        you don't have to pass e.g. a ``boxprops`` dictionary.

    Other parameters
    ----------------
    **kwargs
        Passed to the matplotlib plotting method.
    """
    # Call function
    if len(args) > 2:
        raise ValueError(f'Expected 1-2 positional args, got {len(args)}.')
    if orientation is not None:
        if orientation == 'horizontal':
            kwargs['vert'] = False
        elif orientation != 'vertical':
            raise ValueError(
                'Orientation must be "horizontal" or "vertical", '
                f'got {orientation!r}.'
            )
    obj = func(self, *args, **kwargs)
    if not args:
        return obj

    # Modify results
    # TODO: Pass props keyword args instead? Maybe does not matter.
    lw = _not_none(lw=lw, linewidth=linewidth)
    if fillcolor is None:
        cycler = next(self._get_lines.prop_cycler)
        fillcolor = cycler.get('color', None)
    for key, icolor, ilw in (
        ('boxes', boxcolor, boxlw),
        ('caps', capcolor, caplw),
        ('whiskers', whiskercolor, whiskerlw),
        ('means', meancolor, meanlw),
        ('medians', mediancolor, medianlw),
        ('fliers', fliercolor, flierlw),
    ):
        if key not in obj:  # possible if not rendered
            continue
        artists = obj[key]
        ilw = _not_none(ilw, lw)
        icolor = _not_none(icolor, color)
        for artist in artists:
            if icolor is not None:
                artist.set_color(icolor)
                artist.set_markeredgecolor(icolor)
            if ilw is not None:
                artist.set_linewidth(ilw)
                artist.set_markeredgewidth(ilw)
            if key == 'boxes' and fill:
                patch = mpatches.PathPatch(
                    artist.get_path(), color=fillcolor,
                    alpha=fillalpha, linewidth=0)
                self.add_artist(patch)
            if key == 'fliers':
                if marker is not None:
                    artist.set_marker(marker)
                if markersize is not None:
                    artist.set_markersize(markersize)
    return obj


def violinplot_wrapper(
    self, func, *args,
    lw=None, linewidth=0.7, fillcolor=None, edgecolor='k',
    fillalpha=0.7, orientation=None,
    **kwargs
):
    """
    Adds convenient keyword arguments and changes the default violinplot style
    to match `this matplotlib example \
<https://matplotlib.org/3.1.0/gallery/statistics/customized_violin.html>`__.
    It is also no longer possible to show minima and maxima with whiskers --
    while this is useful for `~matplotlib.axes.Axes.boxplot`\\ s it is
    redundant for `~matplotlib.axes.Axes.violinplot`\\ s.

    Note
    ----
    This function wraps %(methods)s.

    Parameters
    ----------
    *args : 1d or 2d ndarray
        The data array.
    lw, linewidth : float, optional
        The linewidth of the line objects. Default is ``1``.
    edgecolor : color-spec, optional
        The edge color for the violin patches. Default is ``'k'``.
    fillcolor : color-spec, optional
        The violin plot fill color. Default is the next color cycler color.
    fillalpha : float, optional
        The opacity of the violins. Default is ``1``.
    orientation : {None, 'horizontal', 'vertical'}, optional
        Alternative to the native `vert` keyword arg. Controls orientation.
    boxrange, barrange : (float, float), optional
        Percentile ranges for the thick and thin central bars. The defaults
        are ``(25, 75)`` and ``(5, 95)``, respectively.

    Other parameters
    ----------------
    **kwargs
        Passed to `~matplotlib.axes.Axes.violinplot`.
    """
    # Orientation and checks
    if len(args) > 2:
        raise ValueError(f'Expected 1-2 positional args, got {len(args)}.')
    if orientation is not None:
        if orientation == 'horizontal':
            kwargs['vert'] = False
        elif orientation != 'vertical':
            raise ValueError(
                'Orientation must be "horizontal" or "vertical", '
                f'got {orientation!r}.'
            )

    # Sanitize input
    lw = _not_none(lw=lw, linewidth=linewidth)
    if kwargs.pop('showextrema', None):
        warnings._warn_proplot(f'Ignoring showextrema=True.')
    if 'showmeans' in kwargs:
        kwargs.setdefault('means', kwargs.pop('showmeans'))
    if 'showmedians' in kwargs:
        kwargs.setdefault('medians', kwargs.pop('showmedians'))
    kwargs.setdefault('capsize', 0)
    obj = func(
        self, *args,
        showmeans=False, showmedians=False, showextrema=False,
        edgecolor=edgecolor, lw=lw, **kwargs
    )
    if not args:
        return obj

    # Modify body settings
    for artist in obj['bodies']:
        artist.set_alpha(fillalpha)
        artist.set_edgecolor(edgecolor)
        artist.set_linewidths(lw)
        if fillcolor is not None:
            artist.set_facecolor(fillcolor)
    return obj


def _get_transform(self, transform):
    """
    Translates user input transform. Also used in an axes method.
    """
    try:
        from cartopy.crs import CRS
    except ModuleNotFoundError:
        CRS = None
    cartopy = getattr(self, 'name', '') == 'cartopy'
    if (
        isinstance(transform, mtransforms.Transform)
        or CRS and isinstance(transform, CRS)
    ):
        return transform
    elif transform == 'figure':
        return self.figure.transFigure
    elif transform == 'axes':
        return self.transAxes
    elif transform == 'data':
        return PlateCarree() if cartopy else self.transData
    elif cartopy and transform == 'map':
        return self.transData
    else:
        raise ValueError(f'Unknown transform {transform!r}.')


def _update_text(self, props):
    """
    Monkey patch that adds pseudo "border" properties to text objects
    without wrapping the entire class. We override update to facilitate
    updating inset titles.
    """
    props = props.copy()  # shallow copy
    border = props.pop('border', None)
    bordercolor = props.pop('bordercolor', 'w')
    borderinvert = props.pop('borderinvert', False)
    borderwidth = props.pop('borderwidth', 2)
    if border:
        facecolor, bgcolor = self.get_color(), bordercolor
        if borderinvert:
            facecolor, bgcolor = bgcolor, facecolor
        kwargs = {
            'linewidth': borderwidth,
            'foreground': bgcolor,
            'joinstyle': 'miter'
        }
        self.update({
            'color': facecolor,
            'path_effects':
                [mpatheffects.Stroke(**kwargs), mpatheffects.Normal()]
        })
    return type(self).update(self, props)


def text_wrapper(
    self, func,
    x=0, y=0, text='', transform='data',
    family=None, fontfamily=None, fontname=None, fontsize=None, size=None,
    border=False, bordercolor='w', borderwidth=2, borderinvert=False,
    **kwargs
):
    """
    Enables specifying `tranform` with a string name and adds a feature for
    drawing borders around text.

    Note
    ----
    This function wraps %(methods)s.

    Parameters
    ----------
    x, y : float
        The *x* and *y* coordinates for the text.
    text : str
        The text string.
    transform : {'data', 'axes', 'figure'} or \
`~matplotlib.transforms.Transform`, optional
        The transform used to interpret `x` and `y`. Can be a
        `~matplotlib.transforms.Transform` object or a string representing the
        `~matplotlib.axes.Axes.transData`, `~matplotlib.axes.Axes.transAxes`,
        or `~matplotlib.figure.Figure.transFigure` transforms. Default is
        ``'data'``, i.e. the text is positioned in data coordinates.
    fontsize, size : float or str, optional
        The font size. If float, units are inches. If string, units are
        interpreted by `~proplot.utils.units`.
    fontname, fontfamily, family : str, optional
        The font name (e.g. ``'Fira Math'``) or font family name (e.g.
        ``'serif'``). Matplotlib falls back to the system default if not found.
    fontweight, weight, fontstyle, style, fontvariant, variant : str, optional
        Additional font properties. See `~matplotlib.text.Text` for details.
    border : bool, optional
        Whether to draw border around text.
    borderwidth : float, optional
        The width of the text border. Default is ``2`` points.
    bordercolor : color-spec, optional
        The color of the text border. Default is ``'w'``.
    borderinvert : bool, optional
        If ``True``, the text and border colors are swapped.

    Other parameters
    ----------------
    **kwargs
        Passed to `~matplotlib.axes.Axes.text`.
    """
    # Parse input args
    # NOTE: Previously issued warning if fontname did not match any of names
    # in ttflist but this would result in warning for e.g. family='sans-serif'.
    # Matplotlib font API makes it very difficult to inject warning in
    # correct place. Simpler to just
    # NOTE: Do not emit warning if user supplied conflicting properties
    # because matplotlib has like 100 conflicting text properties for which
    # it doesn't emit warnings. Prefer not to fix all of them.
    fontsize = _not_none(fontsize, size)
    fontfamily = _not_none(fontname, fontfamily, family)
    if fontsize is not None:
        kwargs['fontsize'] = units(fontsize, 'pt')
    if fontfamily is not None:
        kwargs['fontfamily'] = fontfamily
    if not transform:
        transform = self.transData
    else:
        transform = _get_transform(self, transform)

    # Apply monkey patch to text object
    # TODO: Why only support this here, and not in arbitrary places throughout
    # rest of matplotlib API? Units engine needs better implementation.
    obj = func(self, x, y, text, transform=transform, **kwargs)
    obj.update = _update_text.__get__(obj)
    obj.update({
        'border': border,
        'bordercolor': bordercolor,
        'borderinvert': borderinvert,
        'borderwidth': borderwidth,
    })
    return obj


def cycle_changer(
    self, func, *args,
    cycle=None, cycle_kw=None,
    label=None, labels=None, values=None,
    legend=None, legend_kw=None,
    colorbar=None, colorbar_kw=None,
    **kwargs
):
    """
    Adds features for controlling colors in the property cycler and drawing
    legends or colorbars in one go.

    Note
    ----
    This function wraps every method that uses the property cycler:
    %(methods)s.

    This wrapper also *standardizes acceptable input* -- these methods now all
    accept 2d arrays holding columns of data, and *x*-coordinates are always
    optional. Note this alters the behavior of `~matplotlib.axes.Axes.boxplot`
    and `~matplotlib.axes.Axes.violinplot`, which now compile statistics on
    *columns* of data instead of *rows*.

    Parameters
    ----------
    cycle : cycle-spec, optional
        The cycle specifer, passed to the `~proplot.constructor.Cycle`
        constructor. If the returned list of colors is unchanged from the
        current axes color cycler, the axes cycle will **not** be reset to the
        first position.
    cycle_kw : dict-like, optional
        Passed to `~proplot.constructor.Cycle`.
    label : float or str, optional
        The legend label to be used for this plotted element.
    labels, values : list of float or list of str, optional
        Used with 2d input arrays. The legend labels or colorbar coordinates
        for each column in the array. Can be numeric or string, and must match
        the number of columns in the 2d array.
    legend : bool, int, or str, optional
        If not ``None``, this is a location specifying where to draw an *inset*
        or *panel* legend from the resulting handle(s). If ``True``, the
        default location is used. Valid locations are described in
        `~proplot.axes.Axes.legend`.
    legend_kw : dict-like, optional
        Ignored if `legend` is ``None``. Extra keyword args for our call
        to `~proplot.axes.Axes.legend`.
    colorbar : bool, int, or str, optional
        If not ``None``, this is a location specifying where to draw an *inset*
        or *panel* colorbar from the resulting handle(s). If ``True``, the
        default location is used. Valid locations are described in
        `~proplot.axes.Axes.colorbar`.
    colorbar_kw : dict-like, optional
        Ignored if `colorbar` is ``None``. Extra keyword args for our call
        to `~proplot.axes.Axes.colorbar`.

    Other parameters
    ----------------
    *args, **kwargs
        Passed to the matplotlib plotting method.

    See also
    --------
    standardize_1d
    proplot.constructor.Cycle
    proplot.constructor.Colors
    """
    # Parse input
    cycle_kw = cycle_kw or {}
    legend_kw = legend_kw or {}
    colorbar_kw = colorbar_kw or {}

    # Test input
    # NOTE: Requires standardize_1d wrapper before reaching this. Also note
    # that the 'x' coordinates are sometimes ignored below.
    name = func.__name__
    if not args:
        return func(self, *args, **kwargs)
    barh = name == 'bar' and kwargs.get('orientation', None) == 'horizontal'
    x, y, *args = args
    if len(args) >= 1 and 'fill_between' in name:
        ys, args = (y, args[0]), args[1:]
    else:
        ys = (y,)

    # Determine and temporarily set cycler
    # NOTE: Axes cycle has no getter, only set_prop_cycle, which sets a
    # prop_cycler attribute on the hidden _get_lines and _get_patches_for_fill
    # objects. This is the only way to query current axes cycler! Should not
    # wrap set_prop_cycle because would get messy and fragile.
    # NOTE: The _get_lines cycler is an *itertools cycler*. Has no length, so
    # we must cycle over it with next(). We try calling next() the same number
    # of times as the length of input cycle. If the input cycle *is* in fact
    # the same, below does not reset the color position, cycles us to start!
    if cycle is not None or cycle_kw:
        # Get the new cycler
        cycle_args = () if cycle is None else (cycle,)
        if y.ndim > 1 and y.shape[1] > 1:  # default samples count
            cycle_kw.setdefault('N', y.shape[1])
        cycle = constructor.Cycle(*cycle_args, **cycle_kw)

        # Get the original property cycle
        # NOTE: Matplotlib saves itertools.cycle(cycler), not the original
        # cycler object, so we must build up the keys again.
        i = 0
        by_key = {}
        cycle_orig = self._get_lines.prop_cycler
        for i in range(len(cycle)):  # use the cycler object length as a guess
            prop = next(cycle_orig)
            for key, value in prop.items():
                if key not in by_key:
                    by_key[key] = set()
                if isinstance(value, (list, np.ndarray)):
                    value = tuple(value)
                by_key[key].add(value)

        # Reset property cycler if it differs
        reset = set(by_key) != set(cycle.by_key())
        if not reset:  # test individual entries
            for key, value in cycle.by_key().items():
                if by_key[key] != set(value):
                    reset = True
                    break
        if reset:
            self.set_prop_cycle(cycle)

    # Custom property cycler additions
    # NOTE: By default matplotlib uses _get_patches_for_fill.get_next_color
    # for scatter properties! So we simultaneously iterate through the
    # _get_lines property cycler and apply them.
    apply = set()  # which keys to apply from property cycler
    if name == 'scatter':
        # Figure out which props should be updated
        keys = {*self._get_lines._prop_keys} - {'color', 'linestyle', 'dashes'}
        for key, prop in (
            ('markersize', 's'),
            ('linewidth', 'linewidths'),
            ('markeredgewidth', 'linewidths'),
            ('markeredgecolor', 'edgecolors'),
            ('alpha', 'alpha'),
            ('marker', 'marker'),
        ):
            prop = kwargs.get(prop, None)
            if key in keys and prop is None:
                apply.add(key)

    # Handle legend labels and
    # WARNING: Most methods that accept 2d arrays use columns of data, but when
    # pandas DataFrame passed to hist, boxplot, or violinplot, rows of data
    # assumed! This is fixed in parse_1d by converting to values.
    ncols = 1
    labels = _not_none(values=values, labels=labels, label=label)
    if name in ('pie', 'boxplot', 'violinplot'):
        if labels is not None:
            kwargs['labels'] = labels
    else:
        ncols = 1 if y.ndim == 1 else y.shape[1]
        if labels is None or isinstance(labels, str):
            labels = [labels] * ncols

    # Handle stacked bar plots
    stacked = kwargs.pop('stacked', False)
    if name in ('bar',):
        width = kwargs.pop('width', 0.8)
        kwargs['height' if barh else 'width'] = (
            width if stacked else width / ncols
        )

    # Plot susccessive columns
    objs = []
    label_leg = None  # for colorbar or legend
    for i in range(ncols):
        # Prop cycle properties
        kw = kwargs.copy()
        if apply:
            props = next(self._get_lines.prop_cycler)
            for key in apply:
                value = props[key]
                if key in ('size', 'markersize'):
                    key = 's'
                elif key in ('linewidth', 'markeredgewidth'):  # translate
                    key = 'linewidths'
                elif key == 'markeredgecolor':
                    key = 'edgecolors'
                kw[key] = value

        # Get x coordinates
        ix, iy = x, ys[0]  # samples
        if name in ('pie',):
            kw['labels'] = _not_none(labels, ix)  # TODO: move to pie wrapper?
        if name in ('bar',):  # adjust
            if not stacked:
                ix = x + (i - ncols / 2 + 0.5) * width / ncols
            elif stacked and iy.ndim > 1:
                key = 'x' if barh else 'bottom'
                kw[key] = _to_indexer(iy)[:, :i].sum(axis=1)

        # Get y coordinates and labels
        if name in ('pie', 'boxplot', 'violinplot'):
            iys = (iy,)  # only ever have one y value, cannot have legend labs
        else:
            # The coordinates
            # WARNING: If stacked=True then we always *ignore* second
            # argument passed to fill_between. Warning should be issued
            # by fill_between_wrapper in this case.
            if stacked and 'fill_between' in name:
                iys = tuple(
                    iy if iy.ndim == 1 else _to_indexer(iy)[:, :ii].sum(axis=1)
                    for ii in (i, i + 1)
                )
            else:
                iys = tuple(
                    iy if iy.ndim == 1 else _to_indexer(iy)[:, i]
                    for iy in ys
                )
            # Possible legend labels
            if len(labels) != ncols:
                raise ValueError(
                    f'Got {ncols} columns in data array, '
                    f'but {len(labels)} labels.'
                )
            label = labels[i]
            values, label_leg = _standard_label(iy, axis=1)
            if label_leg and label is None:
                label = _to_ndarray(values)[i]
            if label is not None:
                kw['label'] = label

        # Build coordinate arguments
        xy = ()
        if barh:  # special, use kwargs only!
            kw.update({'bottom': ix, 'width': iys[0]})
            kw.setdefault('x', kwargs.get('bottom', 0))  # required
        elif name in ('pie', 'hist', 'boxplot', 'violinplot'):
            xy = (*iys,)
        else:  # has x-coordinates, and maybe more than one y
            xy = (ix, *iys)

        # Call plotting function
        obj = func(self, *xy, *args, **kw)
        if isinstance(obj, (list, tuple)) and len(obj) == 1:
            obj = obj[0]
        objs.append(obj)

    # Add colorbar
    if colorbar:
        # Add handles
        loc = self._loc_translate(colorbar, 'colorbar', allow_manual=False)
        if loc not in self._auto_colorbar:
            self._auto_colorbar[loc] = ([], {})
        self._auto_colorbar[loc][0].extend(objs)
        # Add keywords
        if loc != 'fill':
            colorbar_kw.setdefault('loc', loc)
        if label_leg:
            colorbar_kw.setdefault('label', label_leg)
        self._auto_colorbar[loc][1].update(colorbar_kw)

    # Add legend
    if legend:
        # Add handles
        loc = self._loc_translate(legend, 'legend', allow_manual=False)
        if loc not in self._auto_legend:
            self._auto_legend[loc] = ([], {})
        self._auto_legend[loc][0].extend(objs)
        # Add keywords
        if loc != 'fill':
            legend_kw.setdefault('loc', loc)
        if label_leg:
            legend_kw.setdefault('label', label_leg)
        self._auto_legend[loc][1].update(legend_kw)

    # Return
    # WARNING: Make sure plot always returns tuple of objects, and bar always
    # returns singleton unless we have bulk drawn bar plots! Other matplotlib
    # methods call these internally and expect a certain output format!
    if name == 'plot':
        return tuple(objs)  # always return tuple of objects
    elif name in ('boxplot', 'violinplot'):
        return objs[0]  # always return singleton
    else:
        return objs[0] if y.ndim == 1 else tuple(objs)


def _build_discrete_norm(
    data=None, levels=None, values=None,
    norm=None, norm_kw=None, locator=None, locator_kw=None,
    cmap=None, vmin=None, vmax=None, extend='neither', symmetric=False,
    minlength=2,
):
    """
    Build a `~proplot.pcolors.DiscreteNorm` or `~proplot.pcolors.BoundaryNorm`
    from the input arguments. This automatically calculates "nice" level
    boundaries if they were not provided.

    Parameters
    ----------
    data, vmin, vmax, levels, values
        Used to determine the level boundaries.
    norm, norm_kw
        Passed to `~proplot.constructor.Norm`.
    locator, locator_kw
        Passed to `~proplot.constructor.Locator`.
    minlength : int
        The minimum length for level lists.

    Returns
    -------
    norm : `matplotlib.colors.Normalize`
        The normalizer.
    ticks : `numpy.ndarray` or `matplotlib.locator.Locator`
        The axis locator or the tick location candidates.
    """
    # NOTE: Matplotlib colorbar algorithm *cannot* handle descending levels
    # so this function reverses them and adds special attribute to the
    # normalizer. Then colorbar_wrapper reads this attribute and flips the
    # axis and the colormap direction.
    # Check input levels and values
    for key, val in (('levels', levels), ('values', values)):
        if not np.iterable(val):
            continue
        if len(val) < minlength or len(val) >= 2 and any(
            np.sign(np.diff(val)) != np.sign(val[1] - val[0])
        ):
            raise ValueError(
                f'{key!r} must be monotonically increasing or decreasing '
                f'and at least length {minlength}, got {val}.'
            )

    # Get level edges from level centers
    from .config import rc
    ticks = None
    levels = _not_none(levels, rc['image.levels'])
    norm_kw = norm_kw or {}
    locator_kw = locator_kw or {}
    if norm == 'segments':  # TODO: remove
        norm = 'segmented'
    if isinstance(values, Number):
        levels = np.atleast_1d(values)[0] + 1
    elif np.iterable(values) and len(values) == 1:
        levels = [values[0] - 1, values[0] + 1]  # weird but why not
    elif np.iterable(values) and len(values) > 1:
        # Try to generate levels such that a LinearSegmentedNorm will
        # place values ticks at the center of each colorbar level.
        # utils.edges works only for evenly spaced values arrays.
        # We solve for: (x1 + x2)/2 = y --> x2 = 2*y - x1
        # with arbitrary starting point x1. We also start the algorithm
        # on the end with *smaller* differences.
        if norm is None or norm == 'segmented':
            reverse = abs(values[-1] - values[-2]) < abs(values[1] - values[0])
            if reverse:
                values = values[::-1]
            levels = [values[0] - (values[1] - values[0]) / 2]
            for val in values:
                levels.append(2 * val - levels[-1])
            if reverse:
                levels = levels[::-1]
            if any(np.sign(np.diff(levels)) != np.sign(levels[1] - levels[0])):
                levels = edges(values)  # backup plan, weird tick locations
        # Generate levels by finding in-between points in the
        # normalized numeric space, e.g. LogNorm space.
        else:
            inorm = constructor.Norm(norm, **norm_kw)
            levels = inorm.inverse(edges(inorm(values)))
    elif values is not None:
        raise ValueError(
            f'Unexpected input values={values!r}. '
            'Must be integer or list of numbers.'
        )

    # Get default normalizer
    # Only use LinearSegmentedNorm if necessary, because it is slow
    descending = False
    if np.iterable(levels):
        levels, descending = pcolors._check_levels(levels)
    if norm is None:
        norm = 'linear'
        if np.iterable(levels) and len(levels) > 2:
            steps = np.abs(np.diff(levels))
            eps = np.mean(steps) / 1e3
            if np.any(np.abs(np.diff(steps)) >= eps):
                norm = 'segmented'
    if norm == 'segmented':
        if not np.iterable(levels):
            norm = 'linear'  # has same result
        else:
            norm_kw['levels'] = levels
    norm = constructor.Norm(norm, **norm_kw)

    # Use the locator to determine levels
    # Mostly copied from the hidden contour.ContourSet._autolev
    # NOTE: Subsequently, we *only* use the locator to determine ticks if
    # *levels* and *values* were not passed.
    if isinstance(norm, mcolors.BoundaryNorm):
        # Get levels from bounds
        # TODO: Test this feature?
        # NOTE: No warning because we get here internally?
        levels = norm.boundaries
    elif np.iterable(values):
        # Prefer ticks in center
        ticks = np.asarray(values)
    elif np.iterable(levels):
        # Prefer ticks on level edges
        ticks = np.asarray(levels)
    else:
        # Determine levels automatically
        N = levels
        if locator is not None:
            locator = constructor.Locator(locator, **locator_kw)
            ticks = locator
        elif isinstance(norm, mcolors.LogNorm):
            locator = mticker.LogLocator(**locator_kw)
            ticks = locator
        elif isinstance(norm, mcolors.SymLogNorm):
            locator_kw.setdefault('linthresh', norm.linthresh)
            locator = mticker.SymmetricalLogLocator(**locator_kw)
            ticks = locator
        else:
            locator_kw.setdefault('symmetric', symmetric)
            locator = mticker.MaxNLocator(N, min_n_ticks=1, **locator_kw)

        # Get locations
        automin = vmin is None
        automax = vmax is None
        if automin or automax:
            data = ma.masked_invalid(data, copy=False)
            if automin:
                vmin = float(data.min())
            if automax:
                vmax = float(data.max())
            if vmin == vmax or ma.is_masked(vmin) or ma.is_masked(vmax):
                vmin, vmax = 0, 1
        try:
            levels = locator.tick_values(vmin, vmax)
        except RuntimeError:  # too-many-ticks error
            levels = np.linspace(vmin, vmax, N)  # TODO: _autolev used N+1

        # Trim excess levels the locator may have supplied
        # NOTE: This part is mostly copied from _autolev
        if not locator_kw.get('symmetric', None):
            i0, i1 = 0, len(levels)  # defaults
            under, = np.where(levels < vmin)
            if len(under):
                i0 = under[-1]
                if not automin or extend in ('min', 'both'):
                    i0 += 1  # permit out-of-bounds data
            over, = np.where(levels > vmax)
            if len(over):
                i1 = over[0] + 1 if len(over) else len(levels)
                if not automax or extend in ('max', 'both'):
                    i1 -= 1  # permit out-of-bounds data
            if i1 - i0 < 3:
                i0, i1 = 0, len(levels)  # revert
            levels = levels[i0:i1]

        # Compare the no. of levels we *got* (levels) to what we *wanted* (N)
        # If we wanted more than 2 times the result, then add nn - 1 extra
        # levels in-between the returned levels *in normalized space*.
        # Example: A LogNorm gives too few levels, so we select extra levels
        # here, but use the locator for determining tick locations.
        nn = N // len(levels)
        if nn >= 2:
            olevels = norm(levels)
            nlevels = []
            for i in range(len(levels) - 1):
                l1, l2 = olevels[i], olevels[i + 1]
                nlevels.extend(np.linspace(l1, l2, nn + 1)[:-1])
            nlevels.append(olevels[-1])
            levels = norm.inverse(nlevels)

        # Use auto-generated levels for ticks if still None
        if ticks is None:
            ticks = levels

    # Generate DiscreteNorm and update "child" norm with vmin and vmax from
    # levels. This lets the colorbar set tick locations properly!
    if not isinstance(norm, mcolors.BoundaryNorm):
        if getattr(cmap, '_cyclic', None):
            bin_kw = {'step': 0.5, 'extend': 'both'}  # omit end colors
        else:
            bin_kw = {'extend': extend}
        norm = pcolors.DiscreteNorm(
            levels, norm=norm, descending=descending, **bin_kw
        )
    if descending:
        cmap = cmap.reversed()
    return norm, cmap, levels, ticks


def cmap_changer(
    self, func, *args, cmap=None, cmap_kw=None,
    extend='neither', norm=None, norm_kw=None,
    N=None, levels=None, values=None, centers=None, vmin=None, vmax=None,
    symmetric=False, locator=None, locator_kw=None,
    edgefix=None, labels=False, labels_kw=None, fmt=None, precision=2,
    colorbar=False, colorbar_kw=None,
    lw=None, linewidth=None, linewidths=None,
    ls=None, linestyle=None, linestyles=None,
    color=None, colors=None, edgecolor=None, edgecolors=None,
    **kwargs
):
    """
    Adds several new keyword args and features for specifying the colormap,
    levels, and normalizers. Uses the `~proplot.pcolors.DiscreteNorm`
    normalizer to bin data into discrete color levels (see notes).

    Note
    ----
    This function wraps every method that take a `cmap` argument:
    %(methods)s.

    Parameters
    ----------
    cmap : colormap spec, optional
        The colormap specifer, passed to the `~proplot.constructor.Colormap`
        constructor.
    cmap_kw : dict-like, optional
        Passed to `~proplot.constructor.Colormap`.
    norm : normalizer spec, optional
        The colormap normalizer, used to warp data before passing it
        to `~proplot.pcolors.DiscreteNorm`. This is passed to the
        `~proplot.constructor.Norm` constructor.
    norm_kw : dict-like, optional
        Passed to `~proplot.constructor.Norm`.
    extend : {'neither', 'min', 'max', 'both'}, optional
        Where to assign unique colors to out-of-bounds data and draw
        "extensions" (triangles, by default) on the colorbar.
    levels, N : int or list of float, optional
        The number of level edges, or a list of level edges. If the former,
        `locator` is used to generate this many levels at "nice" intervals.
        If the latter, the levels should be monotonically increasing or
        decreasing (note that decreasing levels will only work with ``pcolor``
        plots, not ``contour`` plots). Default is :rc:`image.levels`.

        Since this function also wraps `~matplotlib.axes.Axes.pcolor` and
        `~matplotlib.axes.Axes.pcolormesh`, this means they now
        accept the `levels` keyword arg. You can now discretize your
        colors in a ``pcolor`` plot just like with ``contourf``.
    values, centers : int or list of float, optional
        The number of level centers, or a list of level centers. If provided,
        levels are inferred using `~proplot.utils.edges`. This will override
        any `levels` input.
    symmetric : bool, optional
        If ``True``, auto-generated levels are symmetric about zero.
    vmin, vmax : float, optional
        Used to determine level locations if `levels` is an integer. Actual
        levels may not fall exactly on `vmin` and `vmax`, but the minimum
        level will be no smaller than `vmin` and the maximum level will be
        no larger than `vmax`.

        If `vmin` or `vmax` is not provided, the minimum and maximum data
        values are used.
    locator : locator-spec, optional
        The locator used to determine level locations if `levels` or `values`
        is an integer and `vmin` and `vmax` were not provided. Passed to the
        `~proplot.constructor.Locator` constructor. Default is
        `~matplotlib.ticker.MaxNLocator` with ``levels`` or ``values+1``
        integer levels.
    locator_kw : dict-like, optional
        Passed to `~proplot.constructor.Locator`.
    edgefix : bool, optional
        Whether to fix the the `white-lines-between-filled-contours \
<https://stackoverflow.com/q/8263769/4970632>`__
        and `white-lines-between-pcolor-rectangles \
<https://stackoverflow.com/q/27092991/4970632>`__
        issues. This slows down figure rendering by a bit. Default is
        :rc:`image.edgefix`.
    labels : bool, optional
        For `~matplotlib.axes.Axes.contour`, whether to add contour labels
        with `~matplotlib.axes.Axes.clabel`. For `~matplotlib.axes.Axes.pcolor`
        or `~matplotlib.axes.Axes.pcolormesh`, whether to add labels to the
        center of grid boxes. In the latter case, the text will be black
        when the luminance of the underlying grid box color is >50%%, and
        white otherwise.
    labels_kw : dict-like, optional
        Ignored if `labels` is ``False``. Extra keyword args for the labels.
        For `~matplotlib.axes.Axes.contour`, passed to
        `~matplotlib.axes.Axes.clabel`.  For `~matplotlib.axes.Axes.pcolor`
        or `~matplotlib.axes.Axes.pcolormesh`, passed to
        `~matplotlib.axes.Axes.text`.
    fmt : format-spec, optional
        Passed to the `~proplot.constructor.Norm` constructor, used to format
        number labels. You can also use the `precision` keyword arg.
    precision : int, optional
        Maximum number of decimal places for the number labels.
        Number labels are generated with the
        `~proplot.ticker.SimpleFormatter` formatter, which allows us to
        limit the precision.
    colorbar : bool, int, or str, optional
        If not ``None``, this is a location specifying where to draw an *inset*
        or *panel* colorbar from the resulting mappable. If ``True``, the
        default location is used. Valid locations are described in
        `~proplot.axes.Axes.colorbar`.
    colorbar_kw : dict-like, optional
        Ignored if `colorbar` is ``None``. Extra keyword args for our call
        to `~proplot.axes.Axes.colorbar`.

    Other parameters
    ----------------
    lw, linewidth, linewidths
        The width of `~matplotlib.axes.Axes.contour` lines and
        `~proplot.axes.Axes.parametric` lines. Also the width of lines
        *between* `~matplotlib.axes.Axes.pcolor` boxes,
        `~matplotlib.axes.Axes.pcolormesh` boxes, and
        `~matplotlib.axes.Axes.contourf` filled contours.
    ls, linestyle, linestyles
        As above, but for the line style.
    color, colors, edgecolor, edgecolors
        As above, but for the line color. For `~matplotlib.axes.Axes.contourf`
        plots, if you provide `colors` without specifying the `linewidths`
        or `linestyles`, this argument is used to manually specify the *fill
        colors*. See the `~matplotlib.axes.Axes.contourf` documentation for
        details.
    *args, **kwargs
        Passed to the matplotlib plotting method.

    See also
    --------
    standardize_2d
    proplot.constructor.Colormap
    proplot.constructor.Norm
    proplot.pcolors.DiscreteNorm

    Note
    ----
    The `~proplot.pcolors.DiscreteNorm` normalizer, used with all colormap
    plots, makes sure that your levels always span the full range of colors
    in the colormap, whether `extend` is set to ``'min'``, ``'max'``,
    ``'neither'``, or ``'both'``. By default, when `extend` is not ``'both'``,
    matplotlib seems to just cut off the most intense colors (reserved for
    coloring "out of bounds" data), even though they are not being used.

    This could also be done by limiting the number of colors in the colormap
    lookup table by selecting a smaller ``N`` (see
    `~matplotlib.colors.LinearSegmentedColormap`). Instead, we prefer to
    always build colormaps with high resolution lookup tables, and leave it
    to the `~matplotlib.colors.Normalize` instance to handle discretization
    of the color selections.
    """
    name = func.__name__
    if not args:
        return func(self, *args, **kwargs)

    # Mutable inputs
    cmap_kw = cmap_kw or {}
    norm_kw = norm_kw or {}
    labels_kw = labels_kw or {}
    locator_kw = locator_kw or {}
    colorbar_kw = colorbar_kw or {}

    # Flexible user input
    vmin = _not_none(vmin=vmin, norm_kw_vmin=norm_kw.pop('vmin', None))
    vmax = _not_none(vmax=vmax, norm_kw_vmax=norm_kw.pop('vmax', None))
    values = _not_none(values=values, centers=centers)
    edgefix = _not_none(edgefix, rc['image.edgefix'])
    linewidths = _not_none(lw=lw, linewidth=linewidth, linewidths=linewidths)
    linestyles = _not_none(ls=ls, linestyle=linestyle, linestyles=linestyles)
    colors = _not_none(
        color=color, colors=colors, edgecolor=edgecolor, edgecolors=edgecolors,
    )
    levels = _not_none(
        N=N, levels=levels, norm_kw_levels=norm_kw.pop('levels', None),
        default=rc['image.levels']
    )

    # Get colormap, but do not use cmap when 'colors' are passed to contour()
    # or to contourf() -- the latter only when 'linewidths' and 'linestyles'
    # are also *not* passed. This wrapper lets us add "edges" to contourf
    # plots by calling contour() after contourf() if 'linewidths' or
    # 'linestyles' are explicitly passed, but do not want to disable the
    # native matplotlib feature for manually coloring filled contours.
    # https://matplotlib.org/3.1.1/api/_as_gen/matplotlib.axes.Axes.contourf
    add_contours = (
        name in ('contourf', 'tricontourf')
        and (linewidths is not None or linestyles is not None)
    )
    no_cmap = colors is not None and (
        name in ('contour', 'tricontour')
        or name in ('contourf', 'tricontourf') and not add_contours
    )
    if no_cmap:
        if cmap is not None:
            warnings._warn_proplot(
                f'Ignoring input colormap cmap={cmap!r}, using input colors '
                f'colors={colors!r} instead.'
            )
            cmap = None
        if name in ('contourf', 'tricontourf'):
            kwargs['colors'] = colors  # this was not done above
            colors = None
    else:
        cmap = constructor.Colormap(
            _not_none(cmap, rc['image.cmap']), **cmap_kw
        )
        if getattr(cmap, '_cyclic', None) and extend != 'neither':
            warnings._warn_proplot(
                f'Cyclic colormap requires extend="neither". '
                f'Overriding user input extend={extend!r}.'
            )
            extend = 'neither'

    # Translate standardized keyword arguments back into the keyword args
    # accepted by native matplotlib methods. Also disable edgefix if user want
    # to customize the "edges".
    ignore = []
    style_kw = STYLE_ARGS_TRANSLATE.get(name, None)
    for key, value in (
        ('colors', colors),
        ('linewidths', linewidths),
        ('linestyles', linestyles)
    ):
        if add_contours or value is None:
            continue
        if not style_kw:  # no known conversion table
            ignore.append(key)
            continue
        edgefix = False  # disable edgefix when specifying borders!
        kwargs[style_kw[key]] = value
    if ignore:
        warnings._warn_proplot(
            f'Ignoring keyword arguments for {name!r}: '
            + ', '.join(map(repr, ignore)) + '.'
        )

    # Build colormap normalizer and update keyword args
    # NOTE: Standard algorithm for obtaining default levels does not work
    # for hexbin, because it colors *counts*, not data values!
    ticks = None
    if cmap is not None and name not in ('hexbin',):
        norm, cmap, levels, ticks = _build_discrete_norm(
            args[-1],  # sample data for getting suitable levels
            levels=levels, values=values,
            norm=norm, norm_kw=norm_kw,
            locator=locator, locator_kw=locator_kw,
            cmap=cmap, vmin=vmin, vmax=vmax, extend=extend,
            symmetric=symmetric,
            minlength=(1 if name in ('contour', 'tricontour') else 2),
        )
    if not no_cmap:
        kwargs['cmap'] = cmap
    if norm is not None:
        kwargs['norm'] = norm
    if name in ('contour', 'contourf', 'tricontour', 'tricontourf'):
        kwargs['levels'] = levels
        kwargs['extend'] = extend
    if name in ('parametric',):
        kwargs['values'] = values

    # Call function, possibly twice to add 'edges' to contourf plot
    obj = func(self, *args, **kwargs)
    obj.extend = extend  # normally 'extend' is just for contour/contourf
    if ticks is not None:
        obj.ticks = ticks  # a Locator or ndarray used for controlling ticks
    if add_contours:
        colors = _not_none(colors, 'k')
        self.contour(
            *args, levels=levels, linewidths=linewidths,
            linestyles=linestyles, colors=colors
        )

    # Apply labels
    # TODO: Add quiverkey to this!
    if labels:
        # Formatting for labels
        fmt = _not_none(labels_kw.pop('fmt', None), fmt, 'simple')
        fmt = constructor.Formatter(fmt, precision=precision)

        # Use clabel method
        if name in ('contour', 'contourf', 'tricontour', 'tricontourf'):
            cobj = obj
            colors = None
            if name in ('contourf', 'tricontourf'):
                lums = [
                    to_xyz(cmap(norm(level)), 'hcl')[2] for level in levels
                ]
                cobj = self.contour(*args, levels=levels, linewidths=0)
                colors = ['w' if lum < 50 else 'k' for lum in lums]
            text_kw = {}
            for key in (*labels_kw,):  # allow dict to change size
                if key not in (
                    'levels', 'fontsize', 'colors', 'inline', 'inline_spacing',
                    'manual', 'rightside_up', 'use_clabeltext',
                ):
                    text_kw[key] = labels_kw.pop(key)
            labels_kw.setdefault('colors', colors)
            labels_kw.setdefault('inline_spacing', 3)
            labels_kw.setdefault('fontsize', rc['small'])
            labs = self.clabel(cobj, fmt=fmt, **labels_kw)
            for lab in labs:
                lab.update(text_kw)

        # Label each box manually
        # See: https://stackoverflow.com/a/20998634/4970632
        elif name in ('pcolor', 'pcolormesh'):
            # Populate the _facecolors attribute, which is initially filled
            # with just a single color
            obj.update_scalarmappable()

            # Get text positions and colors
            labels_kw_ = {'size': rc['small'], 'ha': 'center', 'va': 'center'}
            labels_kw_.update(labels_kw)
            array = obj.get_array()
            paths = obj.get_paths()
            colors = np.asarray(obj.get_facecolors())
            edgecolors = np.asarray(obj.get_edgecolors())
            if len(colors) == 1:  # weird flex but okay
                colors = np.repeat(colors, len(array), axis=0)
            if len(edgecolors) == 1:
                edgecolors = np.repeat(edgecolors, len(array), axis=0)
            for i, (color, path, num) in enumerate(zip(colors, paths, array)):
                if not np.isfinite(num):
                    edgecolors[i, :] = 0
                    continue
                bbox = path.get_extents()
                x = (bbox.xmin + bbox.xmax) / 2
                y = (bbox.ymin + bbox.ymax) / 2
                if 'color' not in labels_kw:
                    _, _, lum = to_xyz(color, 'hcl')
                    if lum < 50:
                        color = 'w'
                    else:
                        color = 'k'
                    labels_kw_['color'] = color
                self.text(x, y, fmt(num), **labels_kw_)
            obj.set_edgecolors(edgecolors)
        else:
            raise RuntimeError(f'Not possible to add labels to {name!r} plot.')

    # Fix white lines between filled contours/mesh, allow user to override!
    # 0.4pt is thick enough to hide lines but thin enough to not add "dots" in
    # corner of pcolor plots. *Never* use this when colormap has opacity.
    # See: https://stackoverflow.com/q/15003353/4970632
    if edgefix and name in (
        'pcolor', 'pcolormesh', 'tripcolor', 'contourf', 'tricontourf'
    ):
        cmap = obj.get_cmap()
        if not cmap._isinit:
            cmap._init()
        if all(cmap._lut[:-1, 3] == 1):  # skip for cmaps with transparency
            if name in ('pcolor', 'pcolormesh', 'tripcolor'):
                obj.set_edgecolor('face')
                obj.set_linewidth(0.4)
            else:
                for contour in obj.collections:
                    contour.set_edgecolor('face')
                    contour.set_linewidth(0.4)
                    contour.set_linestyle('-')

    # Optionally add colorbar
    if colorbar:
        loc = self._loc_translate(colorbar, 'colorbar', allow_manual=False)
        if 'label' not in colorbar_kw and self.figure._auto_format:
            _, label = _standard_label(args[-1])  # last one is data, we assume
            if label:
                colorbar_kw.setdefault('label', label)
        if name in ('parametric',) and values is not None:
            colorbar_kw.setdefault('values', values)
        if loc != 'fill':
            colorbar_kw.setdefault('loc', loc)
        self.colorbar(obj, **colorbar_kw)

    return obj


def legend_wrapper(
    self, handles=None, labels=None, *, ncol=None, ncols=None,
    center=None, order='C', loc=None, label=None, title=None,
    fontsize=None, fontweight=None, fontcolor=None,
    color=None, marker=None, lw=None, linewidth=None,
    dashes=None, linestyle=None, markersize=None, frameon=None, frame=None,
    **kwargs
):
    """
    Adds useful features for controlling legends, including "centered-row"
    legends.

    Note
    ----
    This function wraps `proplot.axes.Axes.legend`
    and `proplot.figure.Figure.legend`.

    Parameters
    ----------
    handles : list of `~matplotlib.artist.Artist`, optional
        List of artists instances, or list of lists of artist instances (see
        the `center` keyword). If ``None``, the artists are retrieved with
        `~matplotlib.axes.Axes.get_legend_handles_labels`.
    labels : list of str, optional
        Matching list of string labels, or list of lists of string labels (see
        the `center` keywod). If ``None``, the labels are retrieved by calling
        `~matplotlib.artist.Artist.get_label` on each
        `~matplotlib.artist.Artist` in `handles`.
    ncol, ncols : int, optional
        The number of columns. `ncols` is an alias, added
        for consistency with `~matplotlib.pyplot.subplots`.
    order : {'C', 'F'}, optional
        Whether legend handles are drawn in row-major (``'C'``) or column-major
        (``'F'``) order. Analagous to `numpy.array` ordering. For some reason
        ``'F'`` was the original matplotlib default. Default is ``'C'``.
    center : bool, optional
        Whether to center each legend row individually. If ``True``, we
        actually draw successive single-row legends stacked on top of each
        other.

        If ``None``, we infer this setting from `handles`. Default is ``True``
        if `handles` is a list of lists; each sublist is used as a *row*
        in the legend. Otherwise, default is ``False``.
    loc : int or str, optional
        The legend location. The following location keys are valid:

        ==================  ================================================
        Location            Valid keys
        ==================  ================================================
        "best" possible     ``0``, ``'best'``, ``'b'``, ``'i'``, ``'inset'``
        upper right         ``1``, ``'upper right'``, ``'ur'``
        upper left          ``2``, ``'upper left'``, ``'ul'``
        lower left          ``3``, ``'lower left'``, ``'ll'``
        lower right         ``4``, ``'lower right'``, ``'lr'``
        center left         ``5``, ``'center left'``, ``'cl'``
        center right        ``6``, ``'center right'``, ``'cr'``
        lower center        ``7``, ``'lower center'``, ``'lc'``
        upper center        ``8``, ``'upper center'``, ``'uc'``
        center              ``9``, ``'center'``, ``'c'``
        ==================  ================================================

    label, title : str, optional
        The legend title. The `label` keyword is also accepted, for consistency
        with `colorbar`.
    fontsize, fontweight, fontcolor : optional
        The font size, weight, and color for legend text.
    color, lw, linewidth, marker, linestyle, dashes, markersize : \
property-spec, optional
        Properties used to override the legend handles. For example, if you
        want a legend that describes variations in line style ignoring
        variations in color, you might want to use ``color='k'``. For now this
        does not include `facecolor`, `edgecolor`, and `alpha`, because
        `~matplotlib.axes.Axes.legend` uses these keyword args to modify the
        frame properties.

    Other parameters
    ----------------
    **kwargs
        Passed to `~matplotlib.axes.Axes.legend`.
    """
    # Parse input args
    # TODO: Legend entries for colormap or scatterplot objects! Idea is we
    # pass a scatter plot or contourf or whatever, and legend is generated by
    # drawing patch rectangles or markers using data values and their
    # corresponding cmap colors! For scatterplots just test get_facecolor()
    # to see if it contains more than one color.
    # TODO: It is *also* often desirable to label a colormap object with
    # one data value. Maybe add a legend option for the *number of samples*
    # or the *sample points* when drawing legends for colormap objects.
    # Look into "legend handlers", might just want to add own handlers by
    # passing handler_map to legend() and get_legend_handles_labels().
    if order not in ('F', 'C'):
        raise ValueError(
            f'Invalid order {order!r}. Choose from '
            '"C" (row-major, default) and "F" (column-major).'
        )
    ncol = _not_none(ncols=ncols, ncol=ncol)
    title = _not_none(label=label, title=title)
    frameon = _not_none(
        frame=frame, frameon=frameon, default=rc['legend.frameon']
    )
    if not np.iterable(handles):  # e.g. a mappable object
        handles = [handles]
    if labels is not None and (not np.iterable(labels) or isinstance(labels, str)):  # noqa: E501
        labels = [labels]
    if title is not None:
        kwargs['title'] = title
    if frameon is not None:
        kwargs['frameon'] = frameon
    if fontsize is not None:
        kwargs['fontsize'] = fontsize

    # Text properties that have to be set after-the-fact
    kw_text = {}
    if fontcolor is not None:
        kw_text['color'] = fontcolor
    if fontweight is not None:
        kw_text['weight'] = fontweight

    # Get axes for legend handle detection
    # TODO: Update this when no longer use "filled panels" for outer legends
    axs = [self]
    if self._panel_hidden:
        if self._panel_parent:  # axes panel
            axs = list(self._iter_axes(hidden=False, children=True))
        else:
            axs = list(self.figure._iter_axes(hidden=False, children=True))

    # Handle list of lists (centered row legends)
    list_of_lists = not any(hasattr(handle, 'get_label') for handle in handles)
    if (
        handles is not None and labels is not None
        and len(handles) != len(labels)
    ):
        raise ValueError(
            f'Got {len(handles)} handles and {len(labels)} labels.'
        )
    if list_of_lists:
        if any(not np.iterable(_) for _ in handles):
            raise ValueError(f'Invalid handles={handles!r}.')
        if not labels:
            labels = [None] * len(handles)
        elif not all(
            np.iterable(_) and not isinstance(_, str) for _ in labels
        ):
            raise ValueError(
                f'Invalid labels={labels!r} for handles={handles!r}.'
            )

    # Parse handles and legends with native matplotlib parser
    if not list_of_lists:
        if isinstance(handles, np.ndarray):
            handles = handles.tolist()
        if isinstance(labels, np.ndarray):
            labels = labels.tolist()
        handles, labels, *_ = mlegend._parse_legend_args(
            axs, handles=handles, labels=labels,
        )
        pairs = list(zip(handles, labels))
    else:
        pairs = []
        for ihandles, ilabels in zip(handles, labels):
            if isinstance(ihandles, np.ndarray):
                ihandles = ihandles.tolist()
            if isinstance(ilabels, np.ndarray):
                ilabels = ilabels.tolist()
            ihandles, ilabels, *_ = mlegend._parse_legend_args(
                axs, handles=ihandles, labels=ilabels,
            )
            pairs.append(list(zip(handles, labels)))

    # Manage pairs in context of 'center' option
    center = _not_none(center, list_of_lists)
    if not center and list_of_lists:  # standardize format based on input
        list_of_lists = False  # no longer is list of lists
        pairs = [pair for ipairs in pairs for pair in ipairs]
    elif center and not list_of_lists:
        list_of_lists = True
        ncol = _not_none(ncol, 3)
        pairs = [
            pairs[i * ncol:(i + 1) * ncol] for i in range(len(pairs))
        ]  # to list of iterables
        ncol = None
    if list_of_lists:  # remove empty lists, pops up in some examples
        pairs = [ipairs for ipairs in pairs if ipairs]

    # Individual legend
    legs = []
    width, height = self.get_size_inches()
    if not center:
        # Optionally change order
        # See: https://stackoverflow.com/q/10101141/4970632
        # Example: If 5 columns, but final row length 3, columns 0-2 have
        # N rows but 3-4 have N-1 rows.
        ncol = _not_none(ncol, 3)
        if order == 'C':
            split = [  # split into rows
                pairs[i * ncol:(i + 1) * ncol]
                for i in range(len(pairs) // ncol + 1)
            ]
            nrowsmax = len(split)  # max possible row count
            nfinalrow = len(split[-1])  # columns in final row
            nrows = (
                [nrowsmax] * nfinalrow + [nrowsmax - 1] * (ncol - nfinalrow)
            )
            fpairs = []
            for col, nrow in enumerate(nrows):  # iterate through cols
                fpairs.extend(split[row][col] for row in range(nrow))
            pairs = fpairs

        # Draw legend
        leg = mlegend.Legend(self, *zip(*pairs), ncol=ncol, loc=loc, **kwargs)
        legs = [leg]

    # Legend with centered rows, accomplished by drawing separate legends for
    # each row. The label spacing/border spacing will be exactly replicated.
    else:
        # Message when overriding some properties
        overridden = []
        kwargs.pop('frameon', None)  # then add back later!
        for override in ('bbox_transform', 'bbox_to_anchor'):
            prop = kwargs.pop(override, None)
            if prop is not None:
                overridden.append(override)
        if ncol is not None:
            warnings._warn_proplot(
                'Detected list of *lists* of legend handles. '
                'Ignoring user input property "ncol".'
            )
        if overridden:
            warnings._warn_proplot(
                f'Ignoring user input properties '
                + ', '.join(map(repr, overridden))
                + ' for centered-row legend.'
            )

        # Determine space we want sub-legend to occupy as fraction of height
        # NOTE: Empirical testing shows spacing fudge factor necessary to
        # exactly replicate the spacing of standard aligned legends.
        fontsize = kwargs.get('fontsize', None) or rc['legend.fontsize']
        spacing = kwargs.get('labelspacing', None) or rc['legend.labelspacing']
        interval = 1 / len(pairs)  # split up axes
        interval = (((1 + spacing * 0.85) * fontsize) / 72) / height

        # Iterate and draw
        # NOTE: We confine possible bounding box in *y*-direction, but do not
        # confine it in *x*-direction. Matplotlib will automatically move
        # left-to-right if you request this.
        ymin, ymax = None, None
        if order == 'F':
            raise NotImplementedError(
                f'When center=True, ProPlot vertically stacks successive '
                'single-row legends. Column-major (order="F") ordering '
                'is un-supported.'
            )
        loc = _not_none(loc, 'upper center')
        if not isinstance(loc, str):
            raise ValueError(
                f'Invalid location {loc!r} for legend with center=True. '
                'Must be a location *string*.'
            )
        elif loc == 'best':
            warnings._warn_proplot(
                'For centered-row legends, cannot use "best" location. '
                'Using "upper center" instead.'
            )

        # Iterate through sublists
        for i, ipairs in enumerate(pairs):
            if i == 1:
                kwargs.pop('title', None)
            if i >= 1 and title is not None:
                i += 1  # extra space!

            # Legend position
            if 'upper' in loc:
                y1 = 1 - (i + 1) * interval
                y2 = 1 - i * interval
            elif 'lower' in loc:
                y1 = (len(pairs) + i - 2) * interval
                y2 = (len(pairs) + i - 1) * interval
            else:  # center
                y1 = 0.5 + interval * len(pairs) / 2 - (i + 1) * interval
                y2 = 0.5 + interval * len(pairs) / 2 - i * interval
            ymin = min(y1, _not_none(ymin, y1))
            ymax = max(y2, _not_none(ymax, y2))

            # Draw legend
            bbox = mtransforms.Bbox([[0, y1], [1, y2]])
            leg = mlegend.Legend(
                self, *zip(*ipairs), loc=loc, ncol=len(ipairs),
                bbox_transform=self.transAxes, bbox_to_anchor=bbox,
                frameon=False, **kwargs
            )
            legs.append(leg)

    # Add legends manually so matplotlib does not remove old ones
    # Also apply override settings
    kw_handle = {}
    outline = rc.fill({
        'linewidth': 'axes.linewidth',
        'edgecolor': 'axes.edgecolor',
        'facecolor': 'axes.facecolor',
        'alpha': 'legend.framealpha',
    })
    for key in (*outline,):
        if key != 'linewidth':
            if kwargs.get(key, None):
                outline.pop(key, None)
    for key, value in (
        ('color', color),
        ('marker', marker),
        ('linewidth', lw),
        ('linewidth', linewidth),
        ('markersize', markersize),
        ('linestyle', linestyle),
        ('dashes', dashes),
    ):
        if value is not None:
            kw_handle[key] = value
    for leg in legs:
        self.add_artist(leg)
        leg.legendPatch.update(outline)  # or get_frame()
        for obj in leg.legendHandles:
            if isinstance(obj, martist.Artist):
                obj.update(kw_handle)
        for obj in leg.get_texts():
            if isinstance(obj, martist.Artist):
                obj.update(kw_text)

    # Draw manual fancy bounding box for un-aligned legend
    # WARNING: The matplotlib legendPatch transform is the default transform,
    # i.e. universal coordinates in points. Means we have to transform
    # mutation scale into transAxes sizes.
    # WARNING: Tempting to use legendPatch for everything but for some reason
    # coordinates are messed up. In some tests all coordinates were just result
    # of get window extent multiplied by 2 (???). Anyway actual box is found in
    # _legend_box attribute, which is accessed by get_window_extent.
    if center and frameon:
        if len(legs) == 1:
            legs[0].set_frame_on(True)  # easy!
        else:
            # Get coordinates
            renderer = self.figure._get_renderer()
            bboxs = [leg.get_window_extent(renderer).transformed(
                self.transAxes.inverted()) for leg in legs]
            xmin, xmax = min(bbox.xmin for bbox in bboxs), max(
                bbox.xmax for bbox in bboxs)
            ymin, ymax = min(bbox.ymin for bbox in bboxs), max(
                bbox.ymax for bbox in bboxs)
            fontsize = (fontsize / 72) / width  # axes relative units
            fontsize = renderer.points_to_pixels(fontsize)
            # Draw and format patch
            patch = mpatches.FancyBboxPatch(
                (xmin, ymin), xmax - xmin, ymax - ymin,
                snap=True, zorder=4.5,
                mutation_scale=fontsize, transform=self.transAxes)
            if kwargs.get('fancybox', rc['legend.fancybox']):
                patch.set_boxstyle('round', pad=0, rounding_size=0.2)
            else:
                patch.set_boxstyle('square', pad=0)
            patch.set_clip_on(False)
            patch.update(outline)
            self.add_artist(patch)
            # Add shadow
            # TODO: This does not work, figure out
            if kwargs.get('shadow', rc['legend.shadow']):
                shadow = mpatches.Shadow(patch, 20, -20)
                self.add_artist(shadow)
            # Add patch to list
            legs = (patch, *legs)

    # Append attributes and return, and set clip property!!! This is critical
    # for tight bounding box calcs!
    for leg in legs:
        leg.set_clip_on(False)
    return legs[0] if len(legs) == 1 else (*legs,)


def colorbar_wrapper(
    self, mappable, values=None,
    extend=None, extendsize=None,
    title=None, label=None,
    grid=None, tickminor=None,
    reverse=False, tickloc=None, ticklocation=None,
    locator=None, ticks=None, maxn=None, maxn_minor=None,
    minorlocator=None, minorticks=None,
    locator_kw=None, minorlocator_kw=None,
    formatter=None, ticklabels=None, formatter_kw=None,
    norm=None, norm_kw=None,  # normalizer to use when passing colors/lines
    orientation='horizontal',
    edgecolor=None, linewidth=None,
    labelsize=None, labelweight=None, labelcolor=None,
    ticklabelsize=None, ticklabelweight=None, ticklabelcolor=None,
    **kwargs
):
    """
    Adds useful features for controlling colorbars.

    Note
    ----
    This function wraps `proplot.axes.Axes.legend`
    and `proplot.figure.Figure.legend`.

    Parameters
    ----------
    mappable : mappable, list of plot handles, list of color-spec, \
or colormap-spec
        There are four options here:

        1. A mappable object. Basically, any object with a ``get_cmap`` method,
           like the objects returned by `~matplotlib.axes.Axes.contourf` and
           `~matplotlib.axes.Axes.pcolormesh`.
        2. A list of "plot handles". Basically, any object with a ``get_color``
           method, like `~matplotlib.lines.Line2D` instances. A colormap will
           be generated from the colors of these objects, and colorbar levels
           will be selected using `values`.  If `values` is ``None``, we try
           to infer them by converting the handle labels returned by
           `~matplotlib.artist.Artist.get_label` to `float`. Otherwise, it is
           set to ``np.linspace(0, 1, len(mappable))``.
        3. A list of hex strings, color string names, or RGB tuples. A colormap
           will be generated from these colors, and colorbar levels will be
           selected using `values`. If `values` is ``None``, it is set to
           ``np.linspace(0, 1, len(mappable))``.
        4. A `~matplotlib.colors.Colormap` instance. In this case, a colorbar
           will be drawn using this colormap and with levels determined by
           `values`. If `values` is ``None``, it is set to
           ``np.linspace(0, 1, cmap.N)``.

    values : list of float, optional
        Ignored if `mappable` is a mappable object. This maps each color or
        plot handle in the `mappable` list to numeric values, from which a
        colormap and normalizer are constructed.
    norm : normalizer spec, optional
        Ignored if `values` is ``None``. The normalizer for converting `values`
        to colormap colors. Passed to `~proplot.constructor.Norm`.
    norm_kw : dict-like, optional
        The normalizer settings. Passed to `~proplot.constructor.Norm`.
    extend : {None, 'neither', 'both', 'min', 'max'}, optional
        Direction for drawing colorbar "extensions" (i.e. references to
        out-of-bounds data with a unique color). These are triangles by
        default. If ``None``, we try to use the ``extend`` attribute on the
        mappable object. If the attribute is unavailable, we use ``'neither'``.
    extendsize : float or str, optional
        The length of the colorbar "extensions" in *physical units*.
        If float, units are inches. If string, units are interpreted
        by `~proplot.utils.units`. Default is :rc:`colorbar.insetextend`
        for inset colorbars and :rc:`colorbar.extend` for outer colorbars.
    reverse : bool, optional
        Whether to reverse the direction of the colorbar.
    tickloc, ticklocation : {'bottom', 'top', 'left', 'right'}, optional
        Where to draw tick marks on the colorbar.
    tickminor : bool, optional
        Whether to add minor ticks to the colorbar with
        `~matplotlib.colorbar.ColorbarBase.minorticks_on`.
    label, title : str, optional
        The colorbar label. The `title` keyword is also accepted for
        consistency with `legend`.
    grid : bool, optional
        Whether to draw "gridlines" between each level of the colorbar.
        Default is :rc:`colorbar.grid`.
    locator, ticks : locator spec, optional
        Used to determine the colorbar tick positions. Passed to the
        `~proplot.constructor.Locator` constructor.
    maxn : int, optional
        Used if `locator` is ``None``. Determines the maximum number of levels
        that are ticked. Default depends on the colorbar length relative
        to the font size. The keyword name "maxn" is meant to mimic
        the `~matplotlib.ticker.MaxNLocator` class name.
    locator_kw : dict-like, optional
        The locator settings. Passed to `~proplot.constructor.Locator`.
    minorlocator, minorticks, maxn_minor, minorlocator_kw
        As with `locator`, `maxn`, and `locator_kw`, but for the minor ticks.
    formatter, ticklabels : formatter spec, optional
        The tick label format. Passed to the `~proplot.constructor.Formatter`
        constructor.
    formatter_kw : dict-like, optional
        The formatter settings. Passed to `~proplot.constructor.Formatter`.
    edgecolor, linewidth : optional
        The edge color and line width for the colorbar outline.
    labelsize, labelweight, labelcolor : optional
        The font size, weight, and color for colorbar label text.
    ticklabelsize, ticklabelweight, ticklabelcolor : optional
        The font size, weight, and color for colorbar tick labels.
    orientation : {'horizontal', 'vertical'}, optional
        The colorbar orientation. You should not have to explicitly set this.

    Other parameters
    ----------------
    **kwargs
        Passed to `~matplotlib.figure.Figure.colorbar`.
    """
    # NOTE: There is a weird problem with colorbars when simultaneously
    # passing levels and norm object to a mappable; fixed by passing vmin/vmax
    # instead of levels. (see: https://stackoverflow.com/q/40116968/4970632).
    # NOTE: Often want levels instead of vmin/vmax, while simultaneously
    # using a Normalize (for example) to determine colors between the levels
    # (see: https://stackoverflow.com/q/42723538/4970632). Workaround makes
    # sure locators are in vmin/vmax range exclusively; cannot match values.
    # NOTE: In legend_wrapper() we try to add to the objects accepted by
    # legend() using handler_map. We can't really do anything similar for
    # colorbars; input must just be insnace of mixin class cm.ScalarMappable
    # Mutable args
    norm_kw = norm_kw or {}
    formatter_kw = formatter_kw or {}
    locator_kw = locator_kw or {}
    minorlocator_kw = minorlocator_kw or {}

    # Parse input args
    label = _not_none(title=title, label=label)
    locator = _not_none(ticks=ticks, locator=locator)
    minorlocator = _not_none(minorticks=minorticks, minorlocator=minorlocator)
    ticklocation = _not_none(tickloc=tickloc, ticklocation=ticklocation)
    formatter = _not_none(ticklabels=ticklabels, formatter=formatter, default='auto')

    # Colorbar kwargs
    # WARNING: PathCollection scatter objects have an extend method!
    # WARNING: Matplotlib 3.3 deprecated 'extend' parameter passed to colorbar()
    # but *also* fails to read 'extend' parameter when added to a pcolor mappable!
    # Need to figure out workaround!
    grid = _not_none(grid, rc['colorbar.grid'])
    if extend is None:
        if isinstance(getattr(mappable, 'extend', None), str):
            extend = mappable.extend or 'neither'
        else:
            extend = 'neither'
    kwargs.update({
        'cax': self,
        'use_gridspec': True,
        'orientation': orientation,
        'spacing': 'uniform',
        'extend': extend,
    })
    kwargs.setdefault('drawedges', grid)

    # Text property keyword args
    kw_label = {}
    if labelsize is not None:
        kw_label['size'] = labelsize
    if labelweight is not None:
        kw_label['weight'] = labelweight
    if labelcolor is not None:
        kw_label['color'] = labelcolor
    kw_ticklabels = {}
    if ticklabelsize is not None:
        kw_ticklabels['size'] = ticklabelsize
    if ticklabelweight is not None:
        kw_ticklabels['weight'] = ticklabelweight
    if ticklabelcolor is not None:
        kw_ticklabels['color'] = ticklabelcolor

    # Special case where auto colorbar is generated from 1d methods, a list is
    # always passed, but some 1d methods (scatter) do have colormaps.
    if (
        np.iterable(mappable)
        and len(mappable) == 1
        and hasattr(mappable[0], 'get_cmap')
    ):
        mappable = mappable[0]

    # For container objects, we just assume color is the same for every item.
    # Works for ErrorbarContainer, StemContainer, BarContainer.
    if (
        np.iterable(mappable)
        and len(mappable) > 0
        and all(isinstance(obj, mcontainer.Container) for obj in mappable)
    ):
        mappable = [obj[0] for obj in mappable]

    # Test if we were given a mappable, or iterable of stuff; note Container
    # and PolyCollection matplotlib classes are iterable.
    cmap = None
    if not isinstance(mappable, (martist.Artist, mcontour.ContourSet)):
        # Any colormap spec, including a list of colors, colormap name, or
        # colormap instance.
        if isinstance(mappable, mcolors.Colormap):
            cmap = mappable
            if values is None:
                values = np.arange(cmap.N)

        # List of colors
        elif np.iterable(mappable) and all(
            isinstance(obj, str) or (np.iterable(obj) and len(obj) in (3, 4))
            for obj in mappable
        ):
            colors = list(mappable)
            cmap = mcolors.ListedColormap(colors, '_no_name')
            if values is None:
                values = np.arange(len(colors))

        # List of artists
        elif np.iterable(mappable) and all(
            hasattr(obj, 'get_color') or hasattr(obj, 'get_facecolor')
            for obj in mappable
        ):
            # Generate colormap from colors
            colors = []
            for obj in mappable:
                if hasattr(obj, 'get_color'):
                    color = obj.get_color()
                else:
                    color = obj.get_facecolor()
                if isinstance(color, np.ndarray):
                    color = color.squeeze()  # e.g. scatter plot
                    if color.ndim != 1:
                        raise ValueError(
                            'Cannot make colorbar from list of artists '
                            f'with more than one color: {color!r}.'
                        )
                colors.append(to_rgb(color))
            cmap = mcolors.ListedColormap(colors, '_no_name')

            # Try to infer values from labels
            if values is None:
                values = []
                for obj in mappable:
                    val = obj.get_label()
                    try:
                        val = float(val)
                    except ValueError:
                        values = np.arange(len(colors))
                        break
                    values.append(val)

        else:
            raise ValueError(
                'Input mappable must be a matplotlib artist, '
                'list of objects, list of colors, or colormap. '
                f'Got {mappable!r}.'
            )

        # Build ad hoc ScalarMappable object from colors
        if cmap is not None:
            locator = _not_none(locator, values)  # tick *all* vals by default
            if np.iterable(mappable) and len(values) != len(mappable):
                raise ValueError(
                    f'Passed {len(values)} values, but only {len(mappable)} '
                    f'objects or colors.'
                )
            norm, *_ = _build_discrete_norm(
                values=values, extend='neither',
                cmap=cmap, norm=norm, norm_kw=norm_kw,
            )
            mappable = mcm.ScalarMappable(norm, cmap)

    # Try to get tick locations from *levels* or from *values* rather than
    # random points along the axis.
    # NOTE: Do not necessarily want e.g. minor tick locations at logminor
    # for LogNorm! In _build_discrete_norm we sometimes select evenly spaced
    # levels in log-space *between* powers of 10, so logminor ticks would be
    # misaligned with levels.
    if locator is None:
        locator = getattr(mappable, 'ticks', None)
        if locator is None:
            # This should only happen if user calls plotting method on native
            # matplotlib axes.
            if isinstance(norm, mcolors.LogNorm):
                locator = 'log'
            elif isinstance(norm, mcolors.SymLogNorm):
                locator = 'symlog'
                locator_kw.setdefault('linthresh', norm.linthresh)
            else:
                locator = 'auto'

        elif not isinstance(locator, mticker.Locator):
            # Get default maxn, try to allot 2em squares per label maybe?
            # NOTE: Cannot use Axes.get_size_inches because this is a
            # native matplotlib axes
            width, height = self.figure.get_size_inches()
            if orientation == 'horizontal':
                scale = 3  # em squares alotted for labels
                length = width * abs(self.get_position().width)
                fontsize = kw_ticklabels.get('size', rc['xtick.labelsize'])
            else:
                scale = 1
                length = height * abs(self.get_position().height)
                fontsize = kw_ticklabels.get('size', rc['ytick.labelsize'])
            maxn = _not_none(maxn, int(length / (scale * fontsize / 72)))
            maxn_minor = _not_none(
                maxn_minor, int(length / (0.5 * fontsize / 72))
            )

            # Get locator
            if tickminor and minorlocator is None:
                step = 1 + len(locator) // max(1, maxn_minor)
                minorlocator = locator[::step]
            step = 1 + len(locator) // max(1, maxn)
            locator = locator[::step]

    # Get extend triangles in physical units
    width, height = self.figure.get_size_inches()
    if orientation == 'horizontal':
        scale = width * abs(self.get_position().width)
    else:
        scale = height * abs(self.get_position().height)
    extendsize = units(_not_none(extendsize, rc['colorbar.extend']))
    extendsize = extendsize / (scale - 2 * extendsize)

    # Draw the colorbar
    locator = constructor.Locator(locator, **locator_kw)
    formatter = constructor.Formatter(formatter, **formatter_kw)
    kwargs.update({
        'ticks': locator,
        'format': formatter,
        'ticklocation': ticklocation,
        'extendfrac': extendsize
    })
    mappable.extend = extend  # matplotlib >=3.3
    cb = self.figure.colorbar(mappable, **kwargs)
    axis = self.xaxis if orientation == 'horizontal' else self.yaxis

    # The minor locator
    # TODO: Document the improved minor locator functionality!
    # NOTE: Colorbar._use_auto_colorbar_locator() is never True because we use
    # the custom DiscreteNorm normalizer. Colorbar._ticks() always called.
    if minorlocator is None:
        if tickminor:
            cb.minorticks_on()
        else:
            cb.minorticks_off()
    elif not hasattr(cb, '_ticker'):
        warnings._warn_proplot(
            'Matplotlib colorbar API has changed. '
            f'Cannot use custom minor tick locator {minorlocator!r}.'
        )
        cb.minorticks_on()  # at least turn them on
    else:
        # Set the minor ticks just like matplotlib internally sets the
        # major ticks. Private API is the only way!
        minorlocator = constructor.Locator(minorlocator, **minorlocator_kw)
        ticks, *_ = cb._ticker(minorlocator, mticker.NullFormatter())
        axis.set_ticks(ticks, minor=True)
        axis.set_ticklabels([], minor=True)

    # Label and tick label settings
    # WARNING: Must use colorbar set_label to set text, calling set_text on
    # the axis will do nothing!
    if label is not None:
        cb.set_label(label)
    axis.label.update(kw_label)
    for obj in axis.get_ticklabels():
        obj.update(kw_ticklabels)

    # Ticks
    xy = axis.axis_name
    for which in ('minor', 'major'):
        kw = rc.category(xy + 'tick.' + which)
        kw.pop('visible', None)
        if edgecolor:
            kw['color'] = edgecolor
        if linewidth:
            kw['width'] = linewidth
        axis.set_tick_params(which=which, **kw)
    axis.set_ticks_position(ticklocation)

    # Fix alpha-blending issues.
    # Cannot set edgecolor to 'face' if alpha non-zero because blending will
    # occur, will get colored lines instead of white ones. Need manual blending
    # NOTE: For some reason cb solids uses listed colormap with always 1.0
    # alpha, then alpha is applied after.
    # See: https://stackoverflow.com/a/35672224/4970632
    cmap = cb.cmap
    if not cmap._isinit:
        cmap._init()
    if any(cmap._lut[:-1, 3] < 1):
        warnings._warn_proplot(
            f'Using manual alpha-blending for {cmap.name!r} colorbar solids.'
        )
        # Generate "secret" copy of the colormap!
        lut = cmap._lut.copy()
        cmap = mcolors.Colormap('_cbar_fix', N=cmap.N)
        cmap._isinit = True
        cmap._init = lambda: None
        # Manually fill lookup table with alpha-blended RGB colors!
        for i in range(lut.shape[0] - 1):
            alpha = lut[i, 3]
            lut[i, :3] = (1 - alpha) * 1 + alpha * lut[i, :3]  # blend *white*
            lut[i, 3] = 1
        cmap._lut = lut
        # Update colorbar
        cb.cmap = cmap
        cb.draw_all()

    # Fix colorbar outline
    kw_outline = {
        'edgecolor': _not_none(edgecolor, rc['axes.edgecolor']),
        'linewidth': _not_none(linewidth, rc['axes.linewidth']),
    }
    if cb.outline is not None:
        cb.outline.update(kw_outline)
    if cb.dividers is not None:
        cb.dividers.update(kw_outline)

    # *Never* rasterize because it causes misalignment with border lines
    if cb.solids:
        cb.solids.set_rasterized(False)
        cb.solids.set_linewidth(0.4)
        cb.solids.set_edgecolor('face')

    # Invert the axis if descending DiscreteNorm
    norm = mappable.norm
    if getattr(norm, '_descending', None):
        axis.set_inverted(True)
    if reverse:  # potentially double reverse, although that would be weird...
        axis.set_inverted(True)
    return cb


def _redirect(func):
    """
    Docorator that calls the basemap version of the function of the
    same name. This must be applied as innermost decorator, which means it must
    be applied on the base axes class, not the basemap axes.
    """
    name = func.__name__
    @functools.wraps(func)
    def _wrapper(self, *args, **kwargs):
        if getattr(self, 'name', '') == 'basemap':
            return getattr(self.projection, name)(*args, ax=self, **kwargs)
        else:
            return func(self, *args, **kwargs)
    _wrapper.__doc__ = None
    return _wrapper


def _norecurse(func):
    """
    Decorator to prevent recursion in basemap method overrides.
    See `this post https://stackoverflow.com/a/37675810/4970632`__.
    """
    name = func.__name__
    func._has_recurred = False
    @functools.wraps(func)
    def _wrapper(self, *args, **kwargs):
        if func._has_recurred:
            # Return the *original* version of the matplotlib method
            func._has_recurred = False
            result = getattr(maxes.Axes, name)(self, *args, **kwargs)
        else:
            # Return the version we have wrapped
            func._has_recurred = True
            result = func(self, *args, **kwargs)
        func._has_recurred = False  # cleanup, in case recursion never occurred
        return result
    return _wrapper


def _wrapper_decorator(driver):
    """
    Generate generic wrapper decorator and dynamically modify the docstring
    to list methods wrapped by this function. Also set `__doc__` to ``None`` so
    that ProPlot fork of automodapi doesn't add these methods to the website
    documentation. Users can still call help(ax.method) because python looks
    for superclass method docstrings if a docstring is empty.
    """
    driver._docstring_orig = driver.__doc__ or ''
    driver._methods_wrapped = []
    proplot_methods = ('parametric', 'heatmap', 'area', 'areax')
    cartopy_methods = ('get_extent', 'set_extent')

    def decorator(func):
        # Define wrapper and suppress documentation
        # We only document wrapper functions, not the methods they wrap
        @functools.wraps(func)
        def _wrapper(self, *args, **kwargs):
            return driver(self, func, *args, **kwargs)
        name = func.__name__
        if name not in proplot_methods:
            _wrapper.__doc__ = None

        # List wrapped methods in the driver function docstring
        # Prevents us from having to both explicitly apply decorators in
        # axes.py and explicitly list functions *again* in this file
        docstring = driver._docstring_orig
        if '%(methods)s' in docstring:
            if name in proplot_methods:
                link = f'`~proplot.axes.Axes.{name}`'
            elif name in cartopy_methods:
                link = f'`~cartopy.mpl.geoaxes.GeoAxes.{name}`'
            else:
                link = f'`~matplotlib.axes.Axes.{name}`'
            methods = driver._methods_wrapped
            if link not in methods:
                methods.append(link)
                string = (
                    ', '.join(methods[:-1])
                    + ',' * int(len(methods) > 2)  # Oxford comma bitches
                    + ' and ' * int(len(methods) > 1)
                    + methods[-1])
                driver.__doc__ = docstring % {'methods': string}
        return _wrapper
    return decorator


# Auto generated decorators. Each wrapper internally calls
# func(self, ...) somewhere.
_add_errorbars = _wrapper_decorator(add_errorbars)
_bar_wrapper = _wrapper_decorator(bar_wrapper)
_barh_wrapper = _wrapper_decorator(barh_wrapper)
_default_latlon = _wrapper_decorator(default_latlon)
_boxplot_wrapper = _wrapper_decorator(boxplot_wrapper)
_default_crs = _wrapper_decorator(default_crs)
_default_transform = _wrapper_decorator(default_transform)
_cmap_changer = _wrapper_decorator(cmap_changer)
_cycle_changer = _wrapper_decorator(cycle_changer)
_fill_between_wrapper = _wrapper_decorator(fill_between_wrapper)
_fill_betweenx_wrapper = _wrapper_decorator(fill_betweenx_wrapper)
_hist_wrapper = _wrapper_decorator(hist_wrapper)
_plot_wrapper = _wrapper_decorator(_plot_wrapper_deprecated)
_scatter_wrapper = _wrapper_decorator(scatter_wrapper)
_standardize_1d = _wrapper_decorator(standardize_1d)
_standardize_2d = _wrapper_decorator(standardize_2d)
_text_wrapper = _wrapper_decorator(text_wrapper)
_violinplot_wrapper = _wrapper_decorator(violinplot_wrapper)

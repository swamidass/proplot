#!/usr/bin/env python3
"""
The starting point for creating custom ProPlot figures and axes.
The `subplots` command is all you'll need to directly use here;
it returns a figure instance and list of axes.

Note that
instead of separating features into their own functions (e.g.
a `generate_panel` function), we specify them right when the
figure is declared.

The reason for this approach is we want to build a
*static figure "scaffolding"* before plotting anything. This
way, ProPlot can hold the axes aspect ratios, panel widths, colorbar
widths, and inter-axes spaces **fixed** (note this was really hard
to do and is **really darn cool**!).

See `~Figure.smart_tight_layout` for details.
"""
import os
import re
import numpy as np
# Local modules, projection sand formatters and stuff
from .rcmod import rc
from .utils import _default, _timer, _counter, units, ic
from . import axistools, gridspec, projs, axes
import functools
import warnings
import matplotlib.pyplot as plt
import matplotlib.scale as mscale
import matplotlib.figure as mfigure
import matplotlib.transforms as mtransforms
# For panel names
_aliases = {
    'bpanel': 'bottompanel',
    'rpanel': 'rightpanel',
    'tpanel': 'toppanel',
    'lpanel': 'leftpanel'
    }

#------------------------------------------------------------------------------#
# Miscellaneous helper functions
#------------------------------------------------------------------------------#
def close():
    """
    Alias for ``matplotlib.pyplot.close('all')``.
    Closes all figures stored in memory. Note this does not delete
    rendered figures in an iPython notebook.
    """
    plt.close('all') # easy peasy

def show():
    """
    Alias for ``matplotlib.pyplot.show()``. Note this command should
    be unnecessary if you are doing inline iPython notebook plotting
    and ran the `~proplot.notebook.nbsetup` command.
    """
    plt.show()

#------------------------------------------------------------------------------#
# Figure class
#------------------------------------------------------------------------------#
class _dict(dict):
    # Helper class
    """
    Simple class for accessing elements with dot notation.
    See `this post <https://stackoverflow.com/a/23689767/4970632>`__.
    """
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__
    __delattr__ = dict.__delitem__

class Figure(mfigure.Figure):
    # Subclass adding some super cool features
    def __init__(self, figsize, gridspec=None, subplots_kw=None,
            tight=None, auto_adjust=True, pad=None, innerpad=None,
            rcreset=True, silent=True, # print various draw statements?
            **kwargs):
        """
        Matplotlib figure with some pizzazz.

        Parameters
        ----------
        figsize : length-2 list of float or str
            Figure size (width, height). If float, units are inches. If string,
            units are interpreted by `~proplot.utils.units`.
        gridspec : None or `~proplot.gridspec.FlexibleGridSpec`
            Required if `tight` is ``True``.
            The gridspec instance used to fill entire figure.
        subplots_kw : None or dict-like
            Required if `tight` is ``True``.
            Container of the keyword arguments used in the initial call
            to `subplots`.
        tight : bool, optional
            When figure is drawn, conform its size to a tight bounding
            box around figure contents, without messing
            up axes aspect ratios and internal spacing?
            See `~Figure.smart_tight_layout` for details.
        auto_adjust : bool, optional
            Alias for ``tight``.
        pad : None, float, or str, optional
            Margin size around the tight bounding box. Ignored if
            `tight` is ``False``. If float, units are inches. If string,
            units are interpreted by `~proplot.utils.units`.
        rcreset : bool, optional
            Whether to reset all `~proplot.rcmod.rc` settings to their
            default values once the figure is drawn.
        silent : bool, optional
            Whether to silence messages related to drawing and printing
            the figure.

        Other parameters
        ----------------
        **kwargs : dict-like
            Passed to `matplotlib.figure.Figure` initializer.
        """
        # Initialize figure with some custom attributes.
        # Whether to reset rcParams wheenver a figure is drawn (e.g. after
        # ipython notebook finishes executing)
        self.main_axes = []  # list of 'main' axes (i.e. not insets or panels)
        self._silent = silent # whether to print message when we resize gridspec
        self._rcreset  = rcreset
        self._smart_pad = units(_default(pad, rc['gridspec.pad']))
        self._smart_innerpad = units(_default(innerpad, rc['gridspec.innerpad']))
        self._extra_pad = 0 # sometimes matplotlib fails, cuts off super title! will add to this
        self._smart_tight = _default(tight, auto_adjust) # note name _tight already taken!
        self._smart_tight_init = True # is figure in its initial state?
        self._span_labels = [] # add axis instances to this, and label position will be updated
        # Gridspec information
        self._gridspec = gridspec # gridspec encompassing drawing area
        self._subplots_kw = _dict(subplots_kw) # extra special settings
        # Figure dimensions
        figsize = [units(size) for size in figsize]
        self.width  = figsize[0] # dimensions
        self.height = figsize[1]
        # Panels, initiate as empty
        self.leftpanel   = axes.EmptyPanel()
        self.bottompanel = axes.EmptyPanel()
        self.rightpanel  = axes.EmptyPanel()
        self.toppanel    = axes.EmptyPanel()
        # Proceed
        super().__init__(figsize=figsize, **kwargs) # python 3 only
        # Initialize suptitle, adds _suptitle attribute
        self.suptitle('')
        self._suptitle_transform = None

    def __getattribute__(self, attr, *args):
        """Add some aliases."""
        attr = _aliases.get(attr, attr)
        return super().__getattribute__(attr, *args)

    def _lock_twins(self):
        """Lock shared axis limits to these axis limits. Used for secondary
        axis with alternate data scale."""
        # Helper func
        def check(lim, scale):
            if scale=='log' and any(np.array(lim)<=0):
                raise ValueError('Axis limits go negative, and "alternate units" axis uses log scale.')
            if scale=='inverse' and any(np.array(lim)<=0):
                raise ValueError('Axis limits cross zero, and "alternate units" axis uses inverse scale.')
            if scale=='height' and any(np.array(lim)>1013.25):
                raise ValueError('Axis limits exceed 1013.25 (hPa), and "alternate units" axis uses height scale.')
        for ax in self.main_axes:
            # Match units for x-axis
            if ax.dualx_scale:
                # Get stuff
                # transform = mscale.scale_factory(twin.get_xscale(), twin.xaxis).get_transform()
                twin = ax.twiny_child
                offset, scale = ax.dualx_scale
                transform = twin.xaxis._scale.get_transform() # private API
                xlim_orig = ax.get_xlim()
                # Check, and set
                check(xlim_orig, twin.get_xscale())
                xlim = transform.inverted().transform(np.array(ylim_orig))
                if np.sign(np.diff(xlim_orig)) != np.sign(np.diff(xlim)): # the transform flipped it, so when we try to set limits, will get flipped again!
                    xlim = xlim[::-1]
                twin.set_xlim(offset + scale*xlim)
            # Match units for y-axis
            if ax.dualy_scale:
                # Get stuff
                # transform = mscale.scale_factory(twin.get_yscale(), ax.yaxis).get_transform()
                twin = ax.twinx_child
                offset, scale = ax.dualy_scale
                transform = twin.yaxis._scale.get_transform() # private API
                ylim_orig = ax.get_ylim()
                check(ylim_orig, twin.get_yscale())
                ylim = transform.inverted().transform(np.array(ylim_orig))
                if np.sign(np.diff(ylim_orig)) != np.sign(np.diff(ylim)): # dunno why needed
                    ylim = ylim[::-1]
                twin.set_ylim(offset + scale*ylim) # extra bit comes after the forward transformation

    def _rowlabels(self, labels, **kwargs):
        """Assign row labels."""
        axs = [ax for ax in self.main_axes if ax._xspan[0]==0]
        if isinstance(labels, str): # common during testing
            labels = [labels]*len(axs)
        if len(labels)!=len(axs):
            raise ValueError(f'Got {len(labels)} labels, but there are {len(axs)} rows.')
        axs = [ax for _,ax in sorted(zip([ax._yspan[0] for ax in axs], axs))]
        for ax,label in zip(axs,labels):
            if label and not ax.rowlabel.get_text():
                # Create a CompositeTransform that converts coordinates to
                # universal dots, then back to axes
                label_to_ax = ax.yaxis.label.get_transform() + ax.transAxes.inverted()
                x, _ = label_to_ax.transform(ax.yaxis.label.get_position())
                ax.rowlabel.update({'x':x, 'text':label, 'visible':True, **kwargs})

    def _collabels(self, labels, **kwargs):
        """Assign column labels."""
        axs = [ax for ax in self.main_axes if ax._yspan[0]==0]
        if isinstance(labels, str):
            labels = [labels]*len(axs)
        if len(labels)!=len(axs):
            raise ValueError(f'Got {len(labels)} labels, but there are {len(axs)} columns.')
        axs = [ax for _,ax in sorted(zip([ax._xspan[0] for ax in axs],axs))]
        for ax,label in zip(axs,labels):
            if label and not ax.collabel.get_text():
                ax.collabel.update({'text':label, **kwargs})

    def _suptitle_setup(self, renderer=None, **kwargs):
        """
        Intelligently determine super title position.

        Notes
        -----
        Seems that `~matplotlib.figure.Figure.draw` is called *more than once*,
        and the title position are appropriately offset only during the
        *later* calls! So need to run this every time.
        """
        # Row label
        # Need to update as axis tick labels are generated and axis label is
        # offset!
        for ax in self.axes:
            if  not isinstance(ax, axes.BaseAxes) or not getattr(ax, 'rowlabel', None):
                continue
            label_to_ax = ax.yaxis.label.get_transform() + ax.transAxes.inverted()
            x, _ = label_to_ax.transform(ax.yaxis.label.get_position())
            ax.rowlabel.update({'x':x, 'ha':'center', 'transform':ax.transAxes})

        # Super title
        # Determine x by the underlying gridspec structure, where main axes lie.
        left = self._subplots_kw.left
        right = self._subplots_kw.right
        if self.leftpanel:
            left += (self._subplots_kw.lwidth + self._subplots_kw.lspace)
        if self.rightpanel:
            right += (self._subplots_kw.rwidth + self._subplots_kw.rspace)
        xpos = left/self.width + 0.5*(self.width - left - right)/self.width
        # Figure out which title on the top-row axes will be offset the most
        # See: https://matplotlib.org/_modules/matplotlib/axis.html#Axis.set_tick_params
        # TODO: Modify self.axes? Maybe add a axes_main and axes_panel
        # attribute or something? Because if have insets and other stuff
        # won't that fuck shit up?
        # WARNING: Have to use private API to figure out whether axis has
        # tick labels or not! And buried in docs (https://matplotlib.org/api/_as_gen/matplotlib.axis.Axis.set_ticklabels.html)
        # label1 refers to left or bottom, label2 to right or top!
        # WARNING: Found with xtickloc='both', xticklabelloc='top' or 'both',
        # got x axis ticks position 'unknown'!
        xlabel = 0 # room for x-label
        title_lev1, title_lev2, title_lev3 = None, None, None
        for axm in self.main_axes:
            if not axm._yspan[0]==0:
                continue
            axs = [ax for ax in (axm, axm.leftpanel, axm.rightpanel,
                axm.toppanel, axm.twinx_child, axm.twiny_child) if ax]
            for ax in axs:
                title_lev1 = ax.title # always will be non-None
                if (ax.title.get_text() and not ax._title_inside) or ax.collabel.get_text():
                    title_lev2 = ax.title
                pos = ax.xaxis.get_ticks_position()
                if pos in ('top', 'unknown', 'default'):
                    label_top = pos!='default' and ax.xaxis.label.get_text()
                    ticklabels_top = ax.xaxis._major_tick_kw['label2On']
                    if ticklabels_top:
                        title_lev3 = ax.title
                        if label_top:
                            xlabel = 1.2*(ax.xaxis.label.get_size()/72)/self.height
                    elif label_top:
                        title_lev3 = ax.title

        # Hacky bugfixes
        # NOTE: Default linespacing is 1.2; it has no get, only a setter.
        # See: https://matplotlib.org/api/text_api.html#matplotlib.text.Text.set_linespacing
        if title_lev3:
            # If title and tick labels on top
            # WARNING: Matplotlib tight subplot will screw up, not detect
            # super title. Adding newlines does not help! We fix this manually.
            line = 2.4
            title = title_lev3
            self._extra_pad = 1.2*(self._suptitle.get_size()/72)/self.height
        elif title_lev2:
            # Just offset suptitle, and matplotlib will recognize it
            line = 1.2
            title = title_lev2
        else:
            # No title present. So, fill with spaces. Does nothing in most
            # cases, but if tick labels are on top, without this step
            # matplotlib tight subplots will not see the suptitle.
            line = 0
            title = title_lev1
            if not title.axes._title_inside:
                title.set_text(' ')
        # Get the transformed position
        if self._suptitle_transform:
            transform = self._suptitle_transform
        else:
            transform = title.axes._title_pos_transform + self.transFigure.inverted()
        ypos = transform.transform(title.axes._title_pos_init)[1]
        line = line*(title.get_size()/72)/self.height + xlabel
        ypos = ypos + line

        # Update settings
        self._suptitle.update({
            'position': (xpos, ypos), 'transform': self.transFigure,
            'ha':'center', 'va':'bottom',
            **kwargs})

    def _auto_smart_tight_layout(self, renderer=None):
        """
        Conditionally call `~Figure.smart_tight_layout`.
        """
        # Only proceed if this wasn't already done, or user did not want
        # the figure to have tight boundaries.
        if not self._smart_tight_init or not self._smart_tight:
            return
        # Bail if we find cartopy axes.
        # 1) If you used set_bounds to zoom into part of a cartopy projection,
        # this can erroneously identify invisible edges of map as being part of boundary
        # 2) If you have gridliner text labels, matplotlib won't detect them.
        # TODO: Fix this? Right now we just let set_bounds fuck up, and bail
        # if gridliner was ever used.
        if any(getattr(ax, '_gridliner_on', None) for ax in self.axes):
            return
        # Proceed
        if not self._silent:
            print('Adjusting gridspec.')
        self.smart_tight_layout(renderer)

    def smart_tight_layout(self, renderer=None):
        """
        This is called automatically when `~Figure.draw` or
        `~Figure.savefig` are called,
        unless the user set `auto_adjust` to ``False`` in their original
        call to `subplots`.

        Conforms figure edges to tight bounding box around content, without
        screwing up subplot aspect ratios, empty spaces, panel sizes, and such.
        Also fixes inner spaces, so that e.g. axis tick labels and axis labels
        do not overlap with other axes (which was even harder to do and is
        insanely awesome).

        Parameters
        ----------
        renderer : None or `~matplotlib.backend_bases.RendererBase`, optional
            The backend renderer.
        """
        #----------------------------------------------------------------------#
        # Put tight box *around* figure
        #----------------------------------------------------------------------#
        # Get bounding box that encompasses *all artists*, compare to bounding
        # box used for saving *figure*
        pad = self._smart_pad
        if self._subplots_kw is None or self._gridspec is None:
            warnings.warn('Could not find "_subplots_kw" or "_gridspec" attributes, cannot get tight layout.')
            self._smart_tight_init = False
            return
        obbox = self.bbox_inches # original bbox
        if not renderer: # cannot use the below on figure save! figure becomes a special FigurePDF class or something
            renderer = self.canvas.get_renderer()
        bbox = self.get_tightbbox(renderer)
        ox, oy, x, y = obbox.intervalx, obbox.intervaly, bbox.intervalx, bbox.intervaly
        if np.any(np.isnan(x) | np.isnan(y)):
            warnings.warn('Bounding box has NaNs, cannot get tight layout.')
            self._smart_tight_init = False
            return
        # Apply new kwargs
        subplots_kw = self._subplots_kw
        left, bottom, right, top = x[0], y[0], ox[1]-x[1], oy[1]-y[1] # want *deltas*
        left = subplots_kw.left - left + pad
        right = subplots_kw.right - right + pad
        bottom = subplots_kw.bottom - bottom + pad
        top = subplots_kw.top - top + pad + self._extra_pad
        subplots_kw.update({'left':left, 'right':right, 'bottom':bottom, 'top':top})

        #----------------------------------------------------------------------#
        # Prevent overlapping axis tick labels and whatnot *within* figure
        #----------------------------------------------------------------------#
        # Find individual groups of axes with touching edges; note could
        # extend up entire figure, if have a sort of jigsaw arrangement
        xspans, yspans = np.empty((0,2)), np.empty((0,2))
        xrange, yrange = np.empty((0,2)), np.empty((0,2))
        def span(ax):
            # Get span, accounting for panels, shared axes, and whether axes
            # has been replaced by colorbar axes in same location.
            bboxs = [(ax._colorbar or ax).get_tightbbox(renderer)]
            for sub in (ax.leftpanel, ax.rightpanel, ax.bottompanel,
                    ax.toppanel, ax.twinx_child, ax.twiny_child):
                if sub:
                    bboxs.append((sub._colorbar or sub).get_tightbbox(renderer))
            xspans_i = np.array([bbox.intervalx for bbox in bboxs])
            yspans_i = np.array([bbox.intervaly for bbox in bboxs])
            xspan = [xspans_i[:,0].min(), xspans_i[:,1].max()]
            yspan = [yspans_i[:,0].min(), yspans_i[:,1].max()]
            return xspan, yspan
        def args(axs):
            # If this is a panel axes, check if user generated a colorbar axes,
            # which stores all the artists/is what we really want to get a
            # tight bounding box for.
            spans_i = [span(ax) for ax in axs]
            xspans_i = np.array([span[0] for span in spans_i])
            yspans_i = np.array([span[1] for span in spans_i])
            xrange_i = np.array([ax._xspan for ax in axs])
            yrange_i = np.array([ax._yspan for ax in axs])
            return np.vstack((xspans, xspans_i)), np.vstack((yspans, yspans_i)), \
                   np.vstack((xrange, xrange_i)), np.vstack((yrange, yrange_i))
        # First for the main axes, then add in the panels; need to test these too!
        # TODO: Automatic spacing between stacked colorbars, between
        # main axes and panels! Also consider disallowing stacked colorbars
        # as inner panels? Or not?
        axs = self.main_axes
        xspans, yspans, xrange, yrange = args(axs)
        for panel in (self.leftpanel, self.bottompanel, self.rightpanel):
            if not panel:
                continue
            xspans, yspans, xrange, yrange = args(panel) # "panel" is actually a list of potentially several panels
        xrange[:,1] += 1 # now, ids are not endpoint inclusive
        yrange[:,1] += 1
        # Construct "wspace" and "hspace" including panel spaces
        wspace_orig, hspace_orig = subplots_kw.wspace, subplots_kw.hspace # originals
        if self.leftpanel:
            wspace_orig = [subplots_kw.lspace, *wspace_orig]
        if self.rightpanel:
            wspace_orig = [*wspace_orig, subplots_kw.rspace]
        if self.bottompanel:
            hspace_orig = [*hspace_orig, subplots_kw.bspace]
        # Find groups of axes with touching left/right sides
        # We generate a list (xgroups), each element corresponding to a
        # column of *space*, containing lists of dictionaries with 'l' and
        # 'r' keys, describing groups of "touching" axes in that column
        xgroups = []
        ncols = axs[0]._ncols
        nrows = axs[0]._nrows
        for cspace in range(1, ncols): # spaces between columns
            groups = []
            for row in range(nrows):
                filt = (yrange[:,0] <= row) & (row < yrange[:,1])
                if sum(filt)<=1: # no interface here
                    continue
                right, = np.where(filt & (xrange[:,0] == cspace))
                left,  = np.where(filt & (xrange[:,1] == cspace))
                if not (left.size==1 and right.size==1):
                    continue # normally both zero, but one can be non-zero e.g. if have left and bottom panel, empty space in corner
                left, right = left[0], right[0]
                added = False
                for group in groups:
                    if left in group['l'] or right in group['r']:
                        group['l'].update((left,))
                        group['r'].update((right,))
                        added = True
                        break
                if not added:
                    groups.append({'l':{left}, 'r':{right}}) # form new group
            xgroups.append(groups)
        # Find groups of axes with touching bottom/top sides
        ygroups = []
        for rspace in range(1, nrows): # spaces between rows
            groups = []
            for col in range(1, ncols):
                filt = (xrange[:,0] <= col) & (col < xrange[:,1])
                top,    = np.where(filt & (yrange[:,0] == rspace))
                bottom, = np.where(filt & (yrange[:,1] == rspace))
                if not (bottom.size==1 and top.size==1):
                    continue
                bottom, top = bottom[0], top[0]
                added = False
                for group in groups:
                    if bottom in group['b'] or top in group['t']:
                        group['b'].update((bottom,))
                        group['t'].update((top,))
                        added = True
                        break
                if not added:
                    groups.append({'b':{bottom}, 't':{top}}) # form new group
            ygroups.append(groups)
        # Correct wspace and hspace
        pad = self._smart_innerpad
        wspace, hspace = [], []
        for space_orig, groups in zip(wspace_orig, xgroups):
            if not groups:
                wspace.append(space_orig)
                continue
            seps = [] # will use *maximum* necessary separation
            for group in groups: # groups with touching edges
                left = max(xspans[idx,1] for idx in group['l']) # axes on left side of column
                right = min(xspans[idx,0] for idx in group['r']) # axes on right side of column
                seps.append((right - left)/self.dpi)
            wspace.append(space_orig - min(seps) + pad)
        for space_orig, groups in zip(hspace_orig, ygroups):
            if not groups:
                hspace.append(space_orig)
                continue
            seps = [] # will use *maximum* necessary separation
            for group in groups: # groups with touching edges
                bottom = min(yspans[idx,0] for idx in group['b'])
                top = max(yspans[idx,1] for idx in group['t'])
                seps.append((bottom - top)/self.dpi)
            hspace.append(space_orig - min(seps) + pad)
        # If had panels, need to pull out args
        lspace, rspace, bspace = 0, 0, 0 # does not matter if no panels
        if self.leftpanel:
            lspace, *wspace = wspace
        if self.rightpanel:
            *wspace, rspace = wspace
        if self.bottompanel:
            *hspace, bspace = hspace
        subplots_kw.update({'wspace':wspace, 'hspace':hspace,
            'lspace':lspace, 'rspace':rspace, 'bspace':bspace})

        #----------------------------------------------------------------------#
        # Apply changes
        #----------------------------------------------------------------------#
        figsize, _, gridspec_kw = _subplots_kwargs(**subplots_kw)
        self._smart_tight_init = False
        self._gridspec.update(**gridspec_kw)
        self.set_size_inches(figsize)

    def draw(self, renderer, *args, **kwargs):
        """
        Fix the "super title" position and automatically adjust the
        main gridspec, then call the parent `~matplotlib.figure.Figure.draw`
        method.
        """
        # Special: Figure out if other titles are present, and if not
        # bring suptitle close to center
        # Also fix spanning labels (they need figure-relative height
        # coordinates, so must be updated when figure shape changes).
        self._lock_twins()
        self._suptitle_setup(renderer) # just applies the spacing
        self._auto_smart_tight_layout(renderer)
        for axis in self._span_labels:
            axis.axes._share_span_label(axis, final=True)
        out = super().draw(renderer, *args, **kwargs)

        # If rc settings have been changed, reset them after drawing is done
        # Usually means we have finished executing a notebook cell
        if not rc._init and self._rcreset:
            if not self._silent:
                print('Resetting rcparams.')
            rc.reset()
        return out

    def savefig(self, filename, **kwargs):
        """
        Fix the "super title" position and automatically adjust the
        main gridspec, then call the parent `~matplotlib.figure.Figure.savefig`
        method.

        Parameters
        ----------
        filename : str
            The file name and path.
        **kwargs
            Passed to the matplotlib `~matplotlib.figure.Figure.savefig` method.
        """
        # Notes
        # * Gridspec object must be updated before figure is printed to
        #     screen in interactive environment; will fail to update after that.
        #     Seems to be glitch, should open thread on GitHub.
        # * To color axes patches, you may have to explicitly pass the
        #     transparent=False kwarg.
        #     Some kwarg translations, to pass to savefig
        if 'alpha' in kwargs:
            kwargs['transparent'] = not bool(kwargs.pop('alpha')) # 1 is non-transparent
        if 'color' in kwargs:
            kwargs['facecolor'] = kwargs.pop('color') # the color
            kwargs['transparent'] = False
        # Finally, save
        self._lock_twins()
        self._suptitle_setup() # just applies the spacing
        self._auto_smart_tight_layout()
        for axis in self._span_labels:
            axis.axes._share_span_label(axis, final=True)
        if not self._silent:
            print(f'Saving to "{filename}".')
        return super().savefig(os.path.expanduser(filename), **kwargs) # specify DPI for embedded raster objects

    def save(self, *args, **kwargs):
        """
        Alias for `~Figure.savefig`.
        """
        return self.savefig(*args, **kwargs)

    def panel_factory(self, subspec, whichpanels=None,
            hspace=None, wspace=None,
            hwidth=None, wwidth=None,
            sharex=None, sharey=None, # external sharing
            sharex_level=3, sharey_level=3,
            sharex_panels=True, sharey_panels=True, # by default share main x/y axes with panel x/y axes
            **kwargs):
        """
        Automatically called by `subplots`.
        Creates "inner" panels, i.e. panels along subplot edges.

        Parameters
        ----------
        subspec : `~matplotlib.gridspec.SubplotSpec`
            The `~matplotlib.gridspec.SubplotSpec` instance onto which
            the main subplot and its panels are drawn.
        whichpanels : None or str, optional
            Whether to draw panels on the left, right, bottom, or top
            sides. Should be a string containing any of the characters
            ``'l'``, ``'r'``, ``'b'``, or ``'t'`` in any order.
            Default is ``'r'``.
        wwidth, hwidth : None, float, or str, optional
            Width of vertical (`wwidth`) and horizontal (`hwidth`)
            panels, respectively. If float, units are inches. If string,
            units are interpreted by `~proplot.utils.units`.
        wspace, hspace : None, float, or str, optional
            Empty space between the main subplot and the panel.
            If float, units are inches. If string,
            units are interpreted by `~proplot.utils.units`.
        sharex_panels, sharey_panels : bool, optional
            Whether to share the *x* axes labels for vertically oriented
            (left/right) panels stacked on top of each other, and the
            *y* axes labels for horizontally oriented (bottom/top) panels
            next to each other. Defaults to ``True``.
        sharex, sharey : None or `~proplot.axes.BaseAxes`, optional
            The axes for "sharing" labels, tick labels, and limits.
            See `subplots` for details.
        sharex_level, sharey_level : {3, 2, 1, 0}, optional
            The axis sharing level.
            See `subplots` for details.

        Todo
        ----
        Make settings specific to left, right, top, bottom panels!
        """
        # Helper function for creating paneled axes.
        width, height = self.width, self.height
        translate = {'bottom':'b', 'top':'t', 'right':'r', 'left':'l'}
        whichpanels = translate.get(whichpanels, whichpanels)
        whichpanels = whichpanels or 'r'
        hwidth = units(_default(hwidth, rc['gridspec.panelwidth'])) # default is panels for plotting stuff, not colorbars
        wwidth = units(_default(wwidth, rc['gridspec.panelwidth']))
        hspace = units(_default(hspace, rc['gridspec.panelspace'])) # teeny tiny space
        wspace = units(_default(wspace, rc['gridspec.panelspace']))
        if any(s.lower() not in 'lrbt' for s in whichpanels):
            raise ValueError(f'Whichpanels argument can contain characters l (left), r (right), b (bottom), or t (top), instead got "{whichpanels}".')

        # Determine rows/columns and indices
        nrows = 1 + sum(1 for i in whichpanels if i in 'bt')
        ncols = 1 + sum(1 for i in whichpanels if i in 'lr')
        sides_lr = [l for l in ['l',None,'r'] if not l or l in whichpanels]
        sides_tb = [l for l in ['t',None,'b'] if not l or l in whichpanels]
        # Detect empty positions and main axes position
        main_pos  = (int('t' in whichpanels), int('l' in whichpanels))
        corners   = {'tl':(0,0),             'tr':(0,main_pos[1]+1),
                     'bl':(main_pos[0]+1,0), 'br':(main_pos[0]+1,main_pos[1]+1)}
        empty_pos = [position for corner,position in corners.items() if
                     corner[0] in whichpanels and corner[1] in whichpanels]

        # Fix wspace/hspace in inches, using the Bbox from get_postition
        # on the subspec object to determine physical width of axes to be created
        # * Consider writing some convenience funcs to automate this unit conversion
        bbox = subspec.get_position(self) # valid since axes not drawn yet
        if hspace is not None:
            hspace = np.atleast_1d(hspace)
            if hspace.size==1:
                hspace = np.repeat(hspace, (nrows-1,))
            boxheight = np.diff(bbox.intervaly)[0]*height
            height = boxheight - hspace.sum()
            hspace = hspace/(height/nrows)
        if wspace is not None:
            wspace = np.atleast_1d(wspace)
            if wspace.size==1:
                wspace = np.repeat(wspace, (ncols-1,))
            boxwidth = np.diff(bbox.intervalx)[0]*width
            width = boxwidth - wspace.sum()
            wspace = wspace/(width/ncols)

        # Figure out hratios/wratios
        # Will enforce (main_width + panel_width)/total_width = 1
        wwidth_ratios = [width - wwidth*(ncols-1)]*ncols
        if wwidth_ratios[0]<0:
            raise ValueError(f'Panel wwidth {wwidth} is too large. Must be less than {width/(nrows-1):.3f}.')
        for i in range(ncols):
            if i!=main_pos[1]: # this is a panel entry
                wwidth_ratios[i] = wwidth
        hwidth_ratios = [height - hwidth*(nrows-1)]*nrows
        if hwidth_ratios[0]<0:
            raise ValueError(f'Panel hwidth {hwidth} is too large. Must be less than {height/(ncols-1):.3f}.')
        for i in range(nrows):
            if i!=main_pos[0]: # this is a panel entry
                hwidth_ratios[i] = hwidth

        # Create subplotspec and draw the axes
        # Will create axes in order of rows/columns so that the "base" axes
        # are always built before the axes to be "shared" with them
        panels = []
        gs = gridspec.FlexibleGridSpecFromSubplotSpec(
                nrows         = nrows,
                ncols         = ncols,
                subplot_spec  = subspec,
                wspace        = wspace,
                hspace        = hspace,
                width_ratios  = wwidth_ratios,
                height_ratios = hwidth_ratios,
                )
        # Draw main axes
        ax = self.add_subplot(gs[main_pos[0], main_pos[1]], **kwargs)
        axmain = ax
        # Draw axes
        panels = {}
        kwpanels = {**kwargs, 'projection':'panel'} # override projection
        kwpanels.pop('number', None) # don't want numbering on panels
        translate = {'b':'bottom', 't':'top', 'l':'left', 'r':'right'} # inverse
        for r,side_tb in enumerate(sides_tb): # iterate top-bottom
            for c,side_lr in enumerate(sides_lr): # iterate left-right
                if (r,c) in empty_pos or (r,c)==main_pos:
                    continue
                side = translate.get(side_tb or side_lr, None)
                ax = self.add_subplot(gs[r,c], panel_side=side, panel_parent=axmain, **kwpanels)
                panels[side] = ax

        # Finally add as attributes, and set up axes sharing
        axmain.bottompanel = panels.get('bottom', axes.EmptyPanel())
        axmain.toppanel    = panels.get('top',    axes.EmptyPanel())
        axmain.leftpanel   = panels.get('left',   axes.EmptyPanel())
        axmain.rightpanel  = panels.get('right',  axes.EmptyPanel())
        if sharex_panels:
            axmain._sharex_panels()
        if sharey_panels:
            axmain._sharey_panels()
        axmain._sharex_setup(sharex, sharex_level)
        axmain._sharey_setup(sharey, sharey_level)
        return axmain

#-------------------------------------------------------------------------------
# Primary plotting function; must be used to create figure/axes if user wants
# to use the other features
#-------------------------------------------------------------------------------
class axes_list(list):
    """
    Magical class that iterates through items and calls respective
    method (or retrieves respective attribute) on each one. For example,
    ``axs.format(color='r')`` colors all axes spines and tick marks red.

    When using `subplots`, the list of axes returned is an instance of
    `axes_list`.
    """
    def __repr__(self):
        # Make clear that this is no ordinary list
        return 'axes_list(' + super().__repr__() + ')'

    def __getitem__(self, key):
        # Return an axes_list version of the slice, or just the axes
        axs = list.__getitem__(self, key)
        if isinstance(key,slice): # i.e. returns a list
            axs = axes_list(axs)
        return axs

    def __getattr__(self, attr):
        # Stealthily return dummy function that actually iterates
        # through each attribute here
        values = [getattr(ax, attr, None) for ax in self]
        if None in values:
            raise AttributeError(f"'{type(self[0])}' object has no method '{attr}'.")
        elif all(callable(value) for value in values):
            @functools.wraps(values[0])
            def iterator(*args, **kwargs):
                ret = []
                for ax in self:
                    res = getattr(ax, attr)(*args, **kwargs)
                    if res is not None:
                        ret += [res]
                return None if not ret else ret[0] if len(ret)==1 else ret
            return iterator
        elif all(not callable(value) for value in values):
            return values[0] if len(values)==1 else values # just return the attribute list
        else:
            raise AttributeError('Mixed methods found.')

# Function for processing input and generating necessary keyword args
def _subplots_kwargs(nrows, ncols, rowmajor=True, aspect=1, wextra=0, hextra=0, # extra space *inside* first axes, due to panels
    # General
    figsize=None, # figure size
    left=None, bottom=None, right=None, top=None, # spaces around edge of main plotting area, in inches
    width=None,  height=None, axwidth=None, axheight=None, journal=None,
    hspace=None, wspace=None, hratios=None, wratios=None, # spacing between axes, in inches (hspace should be bigger, allowed room for title)
    bwidth=None, bspace=None, rwidth=None, rspace=None, lwidth=None, lspace=None, # default to no space between panels
    # Panels
    bottompanel=False, bottompanels=False,
    rightpanel=False,  rightpanels=False,
    leftpanel=False,   leftpanels=False,
    bottomcolorbar=False, bottomcolorbars=False, bottomlegend=False, bottomlegends=False, # convenient aliases that change default features
    rightcolorbar=False,  rightcolorbars=False,  rightlegend=False,  rightlegends=False,
    leftcolorbar=False,   leftcolorbars=False,   leftlegend=False,   leftlegends=False,
    # Panel aliases
    bpanel=None, bpanels=None,
    rpanel=None, rpanels=None,
    lpanel=None, lpanels=None,
    bcolorbar=None, bcolorbars=None, blegend=None, blegends=None,
    rcolorbar=None, rcolorbars=None, rlegend=None, rlegends=None,
    lcolorbar=None, lcolorbars=None, llegend=None, llegends=None,
    ):
    """
    Handle complex keyword args and aliases thereof.
    """
    # Aliases
    # Because why the fuck not?
    bottompanel  = bpanel or bottompanel
    bottompanels = bpanels or bottompanels
    rightpanel   = rpanel or rightpanel
    rightpanels  = rpanels or rightpanels
    leftpanel    = lpanel or leftpanel
    leftpanels   = lpanels or leftpanels
    bottomlegend  = blegend or bottomlegend
    bottomlegends = blegends or bottomlegends # convenient aliases that change default features
    rightlegend   = rlegend or rightlegend
    rightlegends  = rlegends or rightlegends
    leftlegend    = llegend or leftlegend
    leftlegends   = llegends or leftlegends
    bottomcolorbar  = bcolorbar or bottomcolorbar
    bottomcolorbars = bcolorbars or bottomcolorbars
    rightcolorbar   = rcolorbar or rightcolorbar
    rightcolorbars  = rcolorbars or rightcolorbars
    leftcolorbar    = lcolorbar or leftcolorbar
    leftcolorbars   = lcolorbars or leftcolorbars

    # Handle the convenience feature for specifying the desired width/spacing
    # for panels as that suitable for a colorbar or legend
    # NOTE: Ugly but this is mostly boilerplate, shouln't change much
    def panel_props(panel, panels, colorbar, colorbars, legend, legends, width, space):
        if colorbar or colorbars:
            width = _default(width, rc['gridspec.cbar'])
            space = _default(space, rc['gridspec.xlab'])
            panel, panels = colorbar, colorbars
        elif legend or legends:
            width = _default(width, rc['gridspec.legend'])
            space = _default(space, 0)
            panel, panels = legend, legends
        return panel, panels, width, space
    rightpanel, rightpanels, rwidth, rspace, = panel_props(
        rightpanel, rightpanels, rightcolorbar, rightcolorbars,
        rightlegend, rightlegends, rwidth, rspace)
    leftpanel, leftpanels, lwidth, lspace = panel_props(
        leftpanel, leftpanels, leftcolorbar, leftcolorbars,
        leftlegend, leftlegends, lwidth, lspace)
    bottompanel, bottompanels, bwidth, bspace = panel_props(
        bottompanel, bottompanels, bottomcolorbar, bottomcolorbars,
        bottomlegend, bottomlegends, bwidth, bspace)

    # Handle the convenience feature for generating one panel per row/column
    # and one single panel for all rows/columns
    def parse(panel, panels, nmax):
        if panel: # one spanning panel
            panels = [1]*nmax
        elif panels not in (None,False): # can't test truthiness, want user to be allowed to pass numpy vector!
            try:
                panels = list(panels)
            except TypeError:
                panels = [*range(nmax)] # pass True to make panel for each column
        return panels
    bottompanels = parse(bottompanel, bottompanels, ncols)
    rightpanels  = parse(rightpanel,  rightpanels,  nrows)
    leftpanels   = parse(leftpanel,   leftpanels,   nrows)

    # Apply the general defaults
    # Need to do this after number of rows/columns figured out
    wratios = np.atleast_1d(_default(wratios, 1))
    hratios = np.atleast_1d(_default(hratios, 1))
    hspace = np.atleast_1d(_default(hspace, rc['gridspec.title']))
    wspace = np.atleast_1d(_default(wspace, rc['gridspec.inner']))
    if len(wratios)==1:
        wratios = np.repeat(wratios, (ncols,))
    if len(hratios)==1:
        hratios = np.repeat(hratios, (nrows,))
    if len(wspace)==1:
        wspace = np.repeat(wspace, (ncols-1,))
    if len(hspace)==1:
        hspace = np.repeat(hspace, (nrows-1,))
    left   = units(_default(left,   rc['gridspec.ylab']))
    bottom = units(_default(bottom, rc['gridspec.xlab']))
    right  = units(_default(right,  rc['gridspec.nolab']))
    top    = units(_default(top,    rc['gridspec.title']))
    bwidth = units(_default(bwidth, rc['gridspec.cbar']))
    rwidth = units(_default(rwidth, rc['gridspec.cbar']))
    lwidth = units(_default(lwidth, rc['gridspec.cbar']))
    bspace = units(_default(bspace, rc['gridspec.xlab']))
    rspace = units(_default(rspace, rc['gridspec.ylab']))
    lspace = units(_default(lspace, rc['gridspec.ylab']))

    # Determine figure size
    if journal:
        if width or height or axwidth or axheight or figsize:
            raise ValueError('Argument conflict: Specify only a journal size, or the figure dimensions, not both.')
        width, height = journal_size(journal) # if user passed width=<string>, will use that journal size
    if not figsize:
        figsize = (width, height)
    width, height = figsize
    width  = units(width, error=False) # if None, returns None
    height = units(height, error=False)
    axwidth = units(axwidth, error=False)
    axheight = units(axheight, error=False)

    # If width and height are not fixed, determine necessary width/height to
    # preserve the aspect ratio of specified plot
    auto_both = (width is None and height is None)
    auto_width  = (width is None and height is not None)
    auto_height = (height is None and width is not None)
    auto_neither = (width is not None and height is not None)
    bpanel_space = bwidth + bspace if bottompanels else 0
    rpanel_space = rwidth + rspace if rightpanels else 0
    lpanel_space = lwidth + lspace if leftpanels else 0
    # Fix aspect ratio, in light of row and column ratios
    try:
        aspect = aspect[0]/aspect[1]
    except (IndexError,TypeError):
        pass # do nothing
    aspect_fixed = aspect/(wratios[0]/np.mean(wratios)) # e.g. if 2 columns, 5:1 width ratio, change the 'average' aspect ratio
    aspect_fixed = aspect*(hratios[0]/np.mean(hratios))
    # Determine average axes widths/heights
    # Default behavior: axes average 2.0 inches wide
    if auto_width or auto_neither:
        axheights = height - top - bottom - sum(hspace) - bpanel_space
    if auto_height or auto_neither:
        axwidths = width - left - right - sum(wspace) - rpanel_space - lpanel_space
    # If both are auto (i.e. no figure dims specified), this is where
    # we set figure dims according to axwidth and axheight input
    if auto_both: # get stuff directly from axes
        if axwidth is None and axheight is None:
            axwidth = units(rc['gridspec.axwidth'])
        if axheight is not None:
            height = axheight*nrows + top + bottom + sum(hspace) + bpanel_space
            axheights = axheight*nrows
            auto_width = True
        if axwidth is not None:
            width = axwidth*ncols + left + right + sum(wspace) + rpanel_space + lpanel_space
            axwidths = axwidth*ncols
            auto_height = True
        if axwidth is not None and axheight is not None:
            auto_width = auto_height = False
        figsize = (width, height) # again
    # Automatically scale fig dimensions
    # Account for space in subplotspec allotted for "inner panels" (wextra
    # and hextra). We want aspect ratio to apply to the *main* subplot.
    if auto_width:
        axheight = axheights*hratios[0]/sum(hratios)
        axwidth  = (axheight - hextra)*aspect + wextra # bigger w ratio, bigger width
        axwidths = axwidth*sum(wratios)/wratios[0]
        width = axwidths + left + right + sum(wspace) + rpanel_space + lpanel_space
    elif auto_height:
        axwidth = axwidths*wratios[0]/sum(wratios)
        axheight = (axwidth - wextra)/aspect + hextra
        axheights = axheight*sum(hratios)/hratios[0]
        height = axheights + top + bottom + sum(hspace) + bpanel_space
    # Check
    if axwidths<0:
        raise ValueError(f"Not enough room for axes (would have width {axwidths}). Increase width, or reduce spacings 'left', 'right', or 'wspace'.")
    if axheights<0:
        raise ValueError(f"Not enough room for axes (would have height {axheights}). Increase height, or reduce spacings 'top', 'bottom', or 'hspace'.")

    # Necessary arguments to reconstruct this grid
    # Can follow some of the pre-processing
    # TODO: We should actually supply with axwidth, axheight, etc. so that
    # when tight subplot acts, axes dims will stay the same!
    # TODO: How exactly is this used again?
    subplots_kw = _dict({
        'wextra':  wextra,   'hextra': hextra,
        'nrows':   nrows,   'ncols':   ncols,
        'figsize': figsize, 'aspect':  aspect,
        'hspace':  hspace,  'wspace':  wspace,
        'hratios': hratios, 'wratios': wratios,
        'left':   left,   'bottom': bottom, 'right':  right,  'top':     top,
        'bwidth': bwidth, 'bspace': bspace, 'rwidth': rwidth, 'rspace': rspace, 'lwidth': lwidth, 'lspace': lspace,
        'bottompanels': bottompanels, 'leftpanels': leftpanels, 'rightpanels': rightpanels
        })

    # Make sure the 'ratios' and 'spaces' are in physical units (we cast the
    # former to physical units), easier then to add stuff as below
    wspace = wspace.tolist()
    hspace = hspace.tolist()
    wratios = (axwidths*(wratios/sum(wratios))).tolist()
    hratios = (axheights*(hratios/sum(hratios))).tolist()

    # Now add the outer panel considerations (idea is we have panels whose
    # widths/heights are *in inches*, and only allow the main subplots and
    # figure widhts/heights to warp to preserve aspect ratio)
    nrows += int(bool(bottompanels))
    ncols += int(bool(rightpanels)) + int(bool(leftpanels))
    if bottompanels:
        hratios = hratios + [bwidth]
        hspace  = hspace + [bspace]
    if leftpanels:
        wratios = [lwidth] + wratios
        wspace  = [lspace] + wspace
    if rightpanels:
        wratios = wratios + [rwidth]
        wspace  = wspace + [rspace]

    # Create gridspec for outer plotting regions (divides 'main area' from side panels)
    # Scale stuff that gridspec needs to be scaled
    # NOTE: We *no longer* scale wspace/hspace because we expect it to
    # be in same scale as axes ratios, much easier that way and no drawback really
    figsize = (width, height)
    bottom = bottom/height
    left   = left/width
    top    = 1 - top/height
    right  = 1 - right/width
    gridspec_kw = {
        'nrows': nrows, 'ncols': ncols,
        'left': left, 'bottom': bottom, 'right': right, 'top': top, # so far no panels allowed here
        'wspace': wspace,        'hspace': hspace,
        'width_ratios': wratios, 'height_ratios' : hratios,
        } # set wspace/hspace to match the top/bottom spaces
    return figsize, subplots_kw, gridspec_kw

def subplots(array=None, ncols=1, nrows=1,
        order='C', # allow calling with subplots(array)
        emptycols=None, emptyrows=None, # obsolete?
        tight=None, auto_adjust=True, pad=None, innerpad=None,
        rcreset=True, silent=True, # arguments for figure instantiation
        span=None, # bulk apply to x/y axes
        share=None, # bulk apply to x/y axes
        spanx=1,  spany=1,  # custom setting, optionally share axis labels for axes with same xmin/ymin extents
        sharex=3, sharey=3, # for sharing x/y axis limits/scales/locators for axes with matching GridSpec extents, and making ticklabels/labels invisible
        innerpanels=None, innercolorbars=None, innerpanels_kw=None, innercolorbars_kw=None,
        basemap=False, proj=None, projection=None, proj_kw=None, projection_kw=None,
        **kwargs):
    # See `proplot.axes.XYAxes.format`, `proplot.axes.CartopyAxes.format`, and
    # `proplot.axes.BasemapAxes.format` for formatting your figure.
    """
    Analagous to `matplotlib.pyplot.subplots`. Create a figure with a single
    axes or arbitrary grids of axes (any of which can be map projections),
    and optional "panels" along axes or figure edges.

    The parameters are sorted into the following rough sections: subplot grid
    specifications, figure and subplot sizes, axis sharing,
    inner panels, outer panels, and map projections.

    Parameters
    ----------
    ncols, nrows : int, optional
        Number of columns, rows. Ignored if `array` is not ``None``.
        Use these arguments for simpler subplot grids.
    order : {'C', 'F'}, optional
        Whether subplots are numbered in column-major (``'C'``) or row-major
        (``'F'``) order. Analagous to `numpy.array` ordering. This controls
        the order axes appear in the `axs` list, and the order of a-b-c
        labelling when using `~proplot.axes.BaseAxes.format` with ``abc=True``.
    array : None or array-like of int, optional
        2-dimensional array specifying complex grid of subplots. Think of
        this array as a "picture" of your figure. For example, the array
        ``[[1, 1], [2, 3]]`` creates one long subplot in the top row, two
        smaller subplots in the bottom row.

        Integers must range from 1 to the number of plots. For example,
        ``[[1, 4]]`` is invalid.

        ``0`` indicates an empty space. For example, ``[[1, 1, 1], [2, 0, 3]]``
        creates one long subplot in the top row with two subplots in the bottom
        row separated by a space.
    emptyrows, emptycols : None or list of int, optional
        Row, column numbers (starting from 1) that you want empty. Generally
        this is used with `ncols` and `nrows`; with `array`, you can
        just use zeros.

    width, height : float or str, optional
        The figure width and height. If float, units are inches. If string,
        units are interpreted by `~proplot.utils.units` -- for example,
        `width="10cm"` creates a 10cm wide figure.
    figsize : length-2 tuple, optional
        Tuple specifying the figure `(width, height)`.
    journal : None or str, optional
        Conform figure width (and height, if specified) to academic journal
        standards. See `~proplot.gridspec.journal_size` for details.
    axwidth, axheight : float or str, optional
        Sets the average width, height of your axes. If float, units are
        inches. If string, units are interpreted by `~proplot.utils.units`.

        These arguments are convenient where you don't care about the figure
        dimensions and just want your axes to have enough "room".
    aspect : float or length-2 list of floats, optional
        The (average) axes aspect ratio, in numeric form (width divided by
        height) or as (width, height) tuple. If you do not provide
        the `hratios` or `wratios` keyword args, all axes will have
        identical aspect ratios.
    hspace, wspace, hratios, wratios, left, right, top, bottom : optional
        Passed to `~proplot.gridspec.FlexibleGridSpecBase`.

    sharex, sharey, share : {3, 2, 1, 0}, optional
        The "axis sharing level" for the *x* axis, *y* axis, or both
        axes. Options are as follows:

            0. No axis sharing.
            1. Only draw *axis label* on the leftmost column (*y*) or
               bottommost row (*x*) of subplots. Axis tick labels
               still appear on every subplot.
            2. As in 1, but forces the axis limits to be identical. Axis
               tick labels still appear on every subplot.
            3. As in 2, but only show the *axis tick labels* on the
               leftmost column (*y*) or bottommost row (*x*) of subplots.

        This feature can considerably redundancy in your figure.
    spanx, spany, span : bool or {1, 0}, optional
        Whether to use "spanning" axis labels for the *x* axis, *y* axis, or
        both axes. When ``True`` or ``1``, the axis label for the leftmost
        (*y*) or bottommost (*x*) subplot is **centered** on that column or
        row -- i.e. it "spans" that column or row. For example, this means
        for a 3-row figure, you only need 1 y-axis label instead of 3.

        This feature can considerably redundancy in your figure.

        "Spanning" labels also integrate with "shared" axes. For example,
        for a 3-row, 3-column figure, with ``sharey>1`` and ``spany=1``,
        your figure will have **1** *y*-axis label instead of 9.

    innerpanels : str or dict-like, optional
        Specify which axes should have inner "panels".

        Panels are stored on the ``leftpanel``, ``rightpanel``,
        ``bottompanel`` and ``toppanel`` attributes on the axes instance.
        You can also use the aliases ``lpanel``, ``rpanel``, ``bpanel``,
        or ``tpanel``.

        The argument is interpreted as follows:

            * If str, panels are drawn on the same side for all subplots.
              String should contain any of the characters ``'l'`` (left panel),
              ``'r'`` (right panel), ``'t'`` (top panel), or ``'b'`` (bottom panel).
              For example, ``'rt'`` indicates a right and top panel.
            * If dict-like, panels can be drawn on different sides for
              different subplots. For example, `innerpanels={1:'r', (2,3):'l'}`
              indicates that we want to draw a panel on the right side of
              subplot number 1, on the left side of subplots 2 and 3.

    innercolorbars : str or dict-like, optional
        Identical to ``innerpanels``, except the default panel size is more
        appropriate for a colorbar. The panel can then be **filled** with
        a colorbar with, for example, ``ax.leftpanel.colorbar(...)``.
    innerpanels_kw : dict-like, optional
        Keyword args passed to `~Figure.panel_factory`. Controls
        the width/height and spacing of panels.

        Can be dict of properties (applies globally), or **dict of dicts** of
        properties (applies to specific properties, as with `innerpanels`).

        For example, consider a figure with 2 columns and 1 row.
        With ``{'wwidth':1}``, all left/right panels 1 inch wide, while
        with ``{1:{'wwidth':1}, 2:{'wwidth':0.5}}``, the left subplot
        panel is 1 inch wide and the right subplot panel is 0.5 inches wide.

        See `~Figure.panel_factory` for keyword arg options.
    innercolorbars_kw
        Alias for `innerpanels_kw`.

    bottompanel, rightpanel, leftpanel : bool, optional
        Whether to draw an "outer" panel surrounding the entire grid of
        subplots, on the bottom, right, or left sides of the figure.
        This is great for creating global colorbars and legends.
        Default is ``False``.

        Panels are stored on the ``leftpanel``, ``rightpanel``,
        and ``bottompanel`` attributes on the figure instance.
        You can also use the aliases ``lpanel``, ``rpanel``, and ``bpanel``.
    bottompanels, rightpanels, leftpanels : bool or list of int, optional
        As with the `panel` keyword args, but this allows you to
        specify **multiple** panels along each figure edge.
        Default is ``False``.

        The arguments are interpreted as follows:

            * If bool, separate panels **for each column/row of subplots**
              are drawn.
            * If list of int, can specify panels that span **contiguous
              columns/rows**. Usage is similar to the ``array`` argument.

              For example, for a figure with 3 columns, ``bottompanels=[1, 2, 2]``
              draws a panel on the bottom of the first column and spanning the
              bottom of the right 2 columns, and ``bottompanels=[0, 2, 2]``
              only draws a panel underneath the right 2 columns (as with
              `array`, the ``0`` indicates an empty space).

    bottomcolorbar, bottomcolorbars, rightcolorbar,  rightcolorbars, leftcolorbar, leftcolorbars
        Identical to the corresponding ``panel`` and ``panels`` keyword
        args, except the default panel size is more appropriate for a colorbar.

        The panel can then be **filled** with a colorbar with, for example, 
        ``fig.leftpanel.colorbar(...)``.
    bottomlegend, bottomlegends, rightlegend,  rightlegends, leftlegend, leftlegends
        Identical to the corresponding ``panel`` and ``panels`` keyword
        args, except the default panel size is more appropriate for a legend.

        The panel can then be **filled** with a legend with, for example, 
        ``fig.leftpanel.legend(...)``.
    bpanel, bpanels, bcolorbar, bcolorbars, blegend, blegends, rpanel, rpanels, rcolorbar, rcolorbars, rlegend, rlegends, lpanel, lpanels, lcolorbar, lcolorbars, llegend, llegends
        Aliases for equivalent args with ``bottom``, ``right``, and ``left``.
        It's a bit faster, so why not?
    lwidth, rwidth, bwidth, twidth : float, optional
        Width of left, right, bottom, and top panels. If float, units are
        inches. If string, units are interpreted by `~proplot.utils.units`.
    lspace, rspace, bspace, tspace : float, optional
        Space between the "inner" edges of the left, right, bottom, and top
        panels and the edge of the main subplot grid. If float, units are
        inches. If string, units are interpreted by `~proplot.utils.units`.

    projection : str or dict-like, optional
        The map projection name.

        If string, applies to all subplots. If dictionary, can be
        specific to each subplot, as with `innerpanels`.

        For example, consider a figure with 4 columns and 1 row.
        With ``projection={1:'mercator', (2,3):'hammer'}``,
        the leftmost subplot is a Mercator projection, the middle 2 are Hammer
        projections, and the rightmost is a normal x/y axes.
    projection_kw : dict-like, optional
        Keyword arguments passed to `~mpl_toolkits.basemap.Basemap` or
        cartopy `~cartopy.crs.Projection` class on instantiation.

        As with `innerpanels_kw`,
        can be dict of properties (applies globally), or **dict of dicts** of
        properties (applies to specific properties, as with `innerpanels`).
    proj, proj_kw
        Aliases for `projection`, `projection_kw`.
    basemap : bool or dict-like, optional
        Whether to use `~mpl_toolkits.basemap.Basemap` or
        `~cartopy.crs.Projection` for map projections. Defaults to ``False``.

        If boolean, applies to all subplots. If dictionary, can be
        specific to each subplot, as with `innerpanels`.

    Returns
    -------
    f : `Figure`
        The figure instance.
    axs : `axes_list`
        A special list of axes instances. See `axes_list`.

    Other parameters
    ----------------
    auto_adjust, tight, pad, innerpad, rcreset, silent
        Passed to `Figure`.
    **kwargs
        Passed to `~proplot.gridspec.FlexibleGridSpec`.

    See also
    --------
    `~proplot.axes.BaseAxes`, `~proplot.axes.BaseAxes.format`

    Notes
    -----
    * Matplotlib `~matplotlib.axes.Axes.set_aspect` option seems to behave
      strangely for some plots; for this reason we override the ``fix_aspect``
      keyword arg provided by `~mpl_toolkits.basemap.Basemap` and just draw
      the figure with appropriate aspect ratio to begin with. Otherwise, we
      get weird differently-shaped subplots that seem to make no sense.

    * All shared axes will generally end up with the same axis
      limits, scaling, major locators, and minor locators. The
      ``sharex`` and ``sharey`` detection algorithm really is just to get
      instructions to make the tick labels and axis labels **invisible**
      for certain axes.

    Todo
    ----
    * Add options for e.g. `bpanel` keyword args.
    * Fix axes aspect ratio stuff when width/height ratios are not one!
    * Generalize axes sharing for right y-axes and top x-axes. Enable a secondary
      axes sharing mode where we *disable ticklabels and labels*, but *do not
      use the builtin sharex/sharey API*, suitable for complex map projections.
    * For spanning axes labels, right now only detect **x labels on bottom**
      and **ylabels on top**. Generalize for all subplot edges.
    """
    #--------------------------------------------------------------------------#
    # Initial stuff
    #--------------------------------------------------------------------------#
    # Ensure getitem mode is zero; might still be non-zero if had error
    # in 'with context' block
    rc._getitem_mode = 0
    # Helper functions
    translate = lambda p: {'bottom':'b', 'top':'t', 'right':'r', 'left':'l'}.get(p, p)
    auto_adjust = _default(tight, auto_adjust)
    def axes_dict(value, kw=False):
        # First build up dictionary
        # Accepts:
        # 1) 'string' or {1:'string1', (2,3):'string2'}
        if not kw:
            if np.iterable(value) and not isinstance(value, (str,dict)):
                value = {num+1: item for num,item in enumerate(value)}
            elif not isinstance(value, dict):
                value = {range(1, num_axes+1): value}
        # 2) {'prop':value} or {1:{'prop':value1}, (2,3):{'prop':value2}}
        else:
            nested = [isinstance(value,dict) for value in value.values()]
            if not any(nested): # any([]) == False
                value = {range(1, num_axes+1): value.copy()}
            elif not all(nested):
                raise ValueError('Pass either of dictionary of key value pairs or a dictionary of dictionaries of key value pairs.')
        # Then unfurl wherever keys contain multiple axes numbers
        kw_out = {}
        for nums,item in value.items():
            nums = np.atleast_1d(nums)
            for num in nums.flat:
                if not kw:
                    kw_out[num] = item
                else:
                    kw_out[num] = item.copy()
        # Verify numbers
        if {*range(1, num_axes+1)} != {*kw_out.keys()}:
            raise ValueError(f'Have {num_axes} axes, but {value} has properties for axes {", ".join(str(i+1) for i in sorted(kw_out.keys()))}.')
        return kw_out

    # Array setup
    if array is None:
        array = np.arange(1,nrows*ncols+1)[...,None]
        if order not in ('C','F'): # better error message
            raise ValueError(f'Invalid order "{order}". Choose from "C" (row-major, default) and "F" (column-major).')
        array = array.reshape((nrows, ncols), order=order) # numpy is row-major, remember
    array = np.array(array) # enforce array type
    if array.ndim==1:
        if order not in ('C','F'): # better error message
            raise ValueError(f'Invalid order "{order}". Choose from "C" (row-major, default) and "F" (column-major).')
        array = array[None,:] if order=='C' else array[:,None] # interpret as single row or column
    # Empty rows/columns feature
    array[array==None] = 0 # use zero for placeholder; otherwise have issues
    if emptycols:
        emptycols = np.atleast_1d(emptycols)
        for col in emptycols.flat:
            array[:,col-1] = 0
    if emptyrows:
        emptyrows = np.atleast_1d(emptyrows)
        for row in emptyrows.flat:
            array[row-1,:] = 0
    # Enforce rule
    nums = np.unique(array[array!=0])
    num_axes = len(nums)
    if {*nums.flat} != {*range(1, num_axes+1)}:
        raise ValueError('Axes numbers must span integers 1 to num_axes (i.e. cannot skip over numbers).')
    nrows = array.shape[0]
    ncols = array.shape[1]

    # Get basemap.Basemap or cartopy.CRS instances for map, and
    # override aspect ratio.
    # NOTE: Cannot have mutable dict as default arg, because it changes the
    # "default" if user calls function more than once! Swap dicts for None.
    basemap = axes_dict(basemap, False)
    proj = axes_dict(_default(proj, projection, 'xy'), False)
    proj_kw = axes_dict(_default(proj_kw, projection_kw, {}), True)
    axes_kw = {num:{} for num in range(1, num_axes+1)}  # stores add_subplot arguments
    for num,name in proj.items():
        # The default, my XYAxes projection
        if name=='xy':
            axes_kw[num]['projection'] = 'xy'
        # Builtin matplotlib polar axes, just use my overridden version
        elif name=='polar':
            axes_kw[num]['projection'] = 'newpolar'
            if num==1:
                kwargs.update(aspect=1)
        # Custom Basemap and Cartopy axes
        elif name:
            package = 'basemap' if basemap[num] else 'cartopy'
            instance, aspect, kwproj = projs.Proj(name, basemap=basemap[num], **proj_kw[num])
            if num==1:
                kwargs.update({'aspect':aspect})
            axes_kw[num].update({'projection':package, 'map_projection':instance})
            axes_kw[num].update(kwproj)
        else:
            raise ValueError('All projection names should be declared. Wut.')

    # Create dictionary of panel toggles and settings
    # Input can be string e.g. 'rl' or dictionary e.g. {(1,2,3):'r', 4:'l'}
    innerpanels    = axes_dict(_default(innerpanels, ''), False)
    innercolorbars = axes_dict(_default(innercolorbars, ''), False)
    innerpanels_kw = axes_dict(_default(innercolorbars_kw, innerpanels_kw, {}), True)
    # Check input, make sure user didn't use boolean or anything
    if not isinstance(innercolorbars, (dict, str)):
        raise ValueError('Must pass string of panel sides or dictionary mapping axes numbers to sides.')
    if not isinstance(innerpanels, (dict, str)):
        raise ValueError('Must pass string of panel sides or dictionary mapping axes numbers to sides.')
    # Apply, with changed defaults for inner colorbars
    for kw in innerpanels_kw.values(): # initialize with empty strings
        kw['whichpanels'] = ''
    for num,which in innerpanels.items():
        kw = innerpanels_kw[num]
        kw['whichpanels'] += translate(which)
        if 'wwidth' not in kw:
            kw['wwidth'] = rc['gridspec.panelwidth']
        if 'hwidth' not in kw:
            kw['hwidth'] = rc['gridspec.panelwidth']
        nh = int('b' in which) + int('t' in which)
        kw['hspace'] = [rc['gridspec.panelspace']]*nh
        nw = int('l' in which) + int('r' in which)
        kw['wspace'] = [rc['gridspec.panelspace']]*nw
    for num,which in innercolorbars.items():
        which = translate(which)
        if not which:
            continue
        kw = innerpanels_kw[num]
        kw['whichpanels'] += which
        if re.search('[bt]', which):
            kw['sharex_panels'] = False
            if 'hwidth' not in kw:
                kw['hwidth'] = rc['gridspec.cbar']
            if 'hspace' not in kw:
                default = [] # TODO: is colorbar on top drawn with labels on top?
                if 't' in which:
                    default.append(rc['gridspec.nolab'])
                if 'b' in which:
                    default.append(rc['gridspec.xlab'])
                kw['hspace'] = default
            if 'hspace' not in kwargs:
                kwargs['hspace'] = rc['gridspec.xlab']
        if re.search('[lr]', which):
            kw['sharey_panels'] = False
            if 'wwidth' not in kw:
                kw['wwidth'] = rc['gridspec.cbar']
            if 'wspace' not in kw:
                default = []
                if 'l' in which:
                    default.append(rc['gridspec.ylab'])
                if 'r' in which:
                    default.append(rc['gridspec.nolab'])
                kw['wspace'] = default
            if 'wspace' not in kwargs:
                kwargs['wspace'] = rc['gridspec.ylab']

    #--------------------------------------------------------------------------#
    # Make gridspec
    #--------------------------------------------------------------------------#
    # Fix subplots_kw aspect ratio in light of inner panels
    # WARNING: Believe this will just *approximate* aspect ratio
    # if we then determine inner panel spacing automatically
    # TODO: How does this change when wratios or hratios are not the same?
    # Think it should be fine.
    kw = innerpanels_kw[1]
    wspace = [units(w) for w in kw['wspace']]
    hspace = [units(h) for h in kw['hspace']]
    kwargs['wextra'] = sum(wspace) + len(wspace)*units(kw['wwidth'])
    kwargs['hextra'] = sum(hspace) + len(hspace)*units(kw['hwidth'])
    # Create gridspec for outer plotting regions (divides 'main area' from side panels)
    figsize, subplots_kw, gridspec_kw = _subplots_kwargs(nrows, ncols, **kwargs)
    gs  = gridspec.FlexibleGridSpec(**gridspec_kw)
    fig = plt.figure(gridspec=gs, figsize=figsize, FigureClass=Figure,
        auto_adjust=auto_adjust, pad=pad, innerpad=innerpad, rcreset=rcreset, silent=silent,
        subplots_kw=subplots_kw)

    #--------------------------------------------------------------------------
    # Manage shared axes/axes with spanning labels
    #--------------------------------------------------------------------------
    # Check input
    sharex = _default(share, sharex)
    sharey = _default(share, sharey)
    spanx  = _default(span, spanx)
    spany  = _default(span, spany)
    if int(sharex) not in range(4) or int(sharey) not in range(4):
        raise ValueError('Axis sharing options sharex/sharey can be 0 (no sharing), 1 (sharing, but keep all tick labels), and 2 (sharing, but only keep one set of tick labels).')
    # Get some axes properties
    # Note that these locations should be **sorted** by axes id
    axes_ids = [np.where(array==i) for i in np.sort(np.unique(array)) if i>0] # 0 stands for empty
    offset = (0, int(bool(subplots_kw.leftpanels))) # offset gridspec idxs from outer panels
    yrange = offset[0] + np.array([[y.min(), y.max()+1] for y,_ in axes_ids]) # yrange is shared columns
    xrange = offset[1] + np.array([[x.min(), x.max()+1] for _,x in axes_ids])
    # Shared axes: generate list of base axes-dependent axes pairs
    # That is, find where the minimum-maximum gridspec extent in 'x' for a
    # given axes matches the minimum-maximum gridspec extent for a base axes
    xgroups_base, xgroups, grouped = [], [], {*()}
    if sharex:
        for i in range(num_axes): # axes now have pseudo-numbers from 0 to num_axes-1
            matches       = (xrange[i,:]==xrange).all(axis=1) # *broadcasting rules apply here*
            matching_axes = np.where(matches)[0] # gives ID number of matching_axes, from 0 to num_axes-1
            if i not in grouped and matching_axes.size>1:
                # Find all axes that have the same gridspec 'x' extents
                xgroups += [matching_axes]
                # Get bottom-most axis with shared x; should be single number
                xgroups_base += [matching_axes[np.argmax(yrange[matching_axes,1])]]
            grouped.update(matching_axes) # bookkeeping; record ids that have been grouped already
    ygroups_base, ygroups, grouped = [], [], {*()}
    if sharey:
        for i in range(num_axes):
            matches       = (yrange[i,:]==yrange).all(axis=1) # *broadcasting rules apply here*
            matching_axes = np.where(matches)[0]
            if i not in grouped and matching_axes.size>1:
                ygroups += [matching_axes]
                ygroups_base += [matching_axes[np.argmin(xrange[matching_axes,0])]] # left-most axis with shared y, for matching_axes
            grouped.update(matching_axes) # bookkeeping; record ids that have been grouped already

    #--------------------------------------------------------------------------
    # Draw axes
    # NOTE: "Spanning axes" are just a *toggle*, actually does nothing until
    # format is used, unlike the more complicated axis-limit fixing stuff
    # done with shared axes.
    #--------------------------------------------------------------------------
    # Draw each axes
    # TODO: Can I add some of this stuff to share_span axes method, so shared
    # axes are detected later on?
    axs = num_axes*[None] # list of axes
    for i in range(num_axes):
        num = i+1
        ax_kw = axes_kw[num]
        if innerpanels_kw[num]['whichpanels']: # non-empty
            axs[i] = fig.panel_factory(gs[slice(*yrange[i,:]), slice(*xrange[i,:])],
                    number=num, spanx=spanx, spany=spany, **ax_kw, **innerpanels_kw[num])
        else:
            axs[i] = fig.add_subplot(gs[slice(*yrange[i,:]), slice(*xrange[i,:])],
                    number=num, spanx=spanx, spany=spany, **ax_kw) # main axes can be a cartopy projection
    # Set up axis sharing
    # NOTE: You must loop through twice, separately for x and y sharing,
    # for some reason.
    if sharex:
        for i in range(num_axes):
            igroup = np.where([i in g for g in xgroups])[0]
            if igroup.size!=1:
                continue
            sharex_ax = axs[xgroups_base[igroup[0]]]
            if axs[i] is sharex_ax:
                continue
            axs[i]._sharex_setup(sharex_ax, sharex)
    if sharey:
        for i in range(num_axes):
            igroup = np.where([i in g for g in ygroups])[0] # np.where works on lists
            if igroup.size!=1:
                continue
            sharey_ax = axs[ygroups_base[igroup[0]]]
            if axs[i] is sharey_ax:
                continue
            axs[i]._sharey_setup(sharey_ax, sharey)
    # Check that axes don't belong to multiple groups
    # This should be impossible unless my code is completely wrong...
    for ax in axs:
        for name,groups in zip(('sharex', 'sharey'), (xgroups, ygroups)):
            if sum(ax in group for group in xgroups)>1:
                raise ValueError(f'Something went wrong; axis {i:d} belongs to multiple {name} groups.')

    #--------------------------------------------------------------------------#
    # Create panel axes
    #--------------------------------------------------------------------------#
    def add_panel(side, panels):
        if not panels:
            return
        pnls = []
        for n in np.unique(panels).flat:
            off = offset[0] if side in ('left','right') else offset[1]
            idx, = np.where(panels==n)
            idx = slice(off + min(idx), off + max(idx) + 1)
            if side=='right':
                subspec = gs[idx,-1]
            elif side=='left':
                subspec = gs[idx,0]
            elif side=='bottom':
                subspec = gs[-1,idx]
            pnl = fig.add_subplot(subspec, panel_side=side, invisible=False, projection='panel')
            pnls += [pnl]
        fig.__setattr__(f'{side}panel', axes_list(pnls))
    add_panel('bottom', subplots_kw.bottompanels)
    add_panel('right',  subplots_kw.rightpanels)
    add_panel('left',   subplots_kw.leftpanels)

    # Return results
    fig.main_axes = axs
    return fig, axes_list(axs)


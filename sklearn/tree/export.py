"""
This module defines export functions for decision trees.
"""

# Authors: Gilles Louppe <g.louppe@gmail.com>
#          Peter Prettenhofer <peter.prettenhofer@gmail.com>
#          Brian Holt <bdholt1@gmail.com>
#          Noel Dawe <noel@dawe.me>
#          Satrajit Gosh <satrajit.ghosh@gmail.com>
#          Trevor Stephens <trev.stephens@gmail.com>
#          Li Li <aiki.nogard@gmail.com>
# License: BSD 3 clause

from numbers import Integral

import numpy as np
import warnings

from ..externals import six
from ..utils.validation import check_is_fitted

from . import _criterion
from . import _tree
from ._reingold_tilford import buchheim, Tree


def _color_brew(n):
    """Generate n colors with equally spaced hues.

    Parameters
    ----------
    n : int
        The number of colors required.

    Returns
    -------
    color_list : list, length n
        List of n tuples of form (R, G, B) being the components of each color.
    """
    color_list = []

    # Initialize saturation & value; calculate chroma & value shift
    s, v = 0.75, 0.9
    c = s * v
    m = v - c

    for h in np.arange(25, 385, 360. / n).astype(int):
        # Calculate some intermediate values
        h_bar = h / 60.
        x = c * (1 - abs((h_bar % 2) - 1))
        # Initialize RGB with same hue & chroma as our color
        rgb = [(c, x, 0),
               (x, c, 0),
               (0, c, x),
               (0, x, c),
               (x, 0, c),
               (c, 0, x),
               (c, x, 0)]
        r, g, b = rgb[int(h_bar)]
        # Shift the initial RGB values to match value and store
        rgb = [(int(255 * (r + m))),
               (int(255 * (g + m))),
               (int(255 * (b + m)))]
        color_list.append(rgb)

    return color_list


class Sentinel(object):
    def __repr__():
        return '"tree.dot"'


SENTINEL = Sentinel()


def plot_tree(decision_tree, max_depth=None, feature_names=None,
              class_names=None, label='all', filled=False,
              leaves_parallel=False, impurity=True, node_ids=False,
              proportion=False, rotate=False, rounded=False,
              special_characters=False, precision=3, ax=None, fontsize=None):
    """Plot a decision tree.

    The sample counts that are shown are weighted with any sample_weights that
    might be present.

    Read more in the :ref:`User Guide <tree>`.

    Parameters
    ----------
    decision_tree : decision tree classifier
        The decision tree to be exported to GraphViz.

    max_depth : int, optional (default=None)
        The maximum depth of the representation. If None, the tree is fully
        generated.

    feature_names : list of strings, optional (default=None)
        Names of each of the features.

    class_names : list of strings, bool or None, optional (default=None)
        Names of each of the target classes in ascending numerical order.
        Only relevant for classification and not supported for multi-output.
        If ``True``, shows a symbolic representation of the class name.

    label : {'all', 'root', 'none'}, optional (default='all')
        Whether to show informative labels for impurity, etc.
        Options include 'all' to show at every node, 'root' to show only at
        the top root node, or 'none' to not show at any node.

    filled : bool, optional (default=False)
        When set to ``True``, paint nodes to indicate majority class for
        classification, extremity of values for regression, or purity of node
        for multi-output.

    leaves_parallel : bool, optional (default=False)
        When set to ``True``, draw all leaf nodes at the bottom of the tree.

    impurity : bool, optional (default=True)
        When set to ``True``, show the impurity at each node.

    node_ids : bool, optional (default=False)
        When set to ``True``, show the ID number on each node.

    proportion : bool, optional (default=False)
        When set to ``True``, change the display of 'values' and/or 'samples'
        to be proportions and percentages respectively.

    rotate : bool, optional (default=False)
        When set to ``True``, orient tree left to right rather than top-down.

    rounded : bool, optional (default=False)
        When set to ``True``, draw node boxes with rounded corners and use
        Helvetica fonts instead of Times-Roman.

    special_characters : bool, optional (default=False)
        When set to ``False``, ignore special characters for PostScript
        compatibility.

    precision : int, optional (default=3)
        Number of digits of precision for floating point in the values of
        impurity, threshold and value attributes of each node.

    ax : matplotlib axis, optional (default=None)
        Axes to plot to. If None, use current axis.

    Examples
    --------
    >>> from sklearn.datasets import load_iris
    >>> from sklearn import tree

    >>> clf = tree.DecisionTreeClassifier()
    >>> iris = load_iris()

    >>> clf = clf.fit(iris.data, iris.target)
    >>> tree.plot_tree(clf)                # doctest: +SKIP

    """
    exporter = _MPLTreeExporter(
        max_depth=max_depth, feature_names=feature_names,
        class_names=class_names, label=label, filled=filled,
        leaves_parallel=leaves_parallel, impurity=impurity, node_ids=node_ids,
        proportion=proportion, rotate=rotate, rounded=rounded,
        special_characters=special_characters, precision=precision,
        fontsize=fontsize)
    exporter.export(decision_tree, ax=ax)


class _BaseTreeExporter(object):
    def get_color(self, value):
        # Find the appropriate color & intensity for a node
        if self.colors['bounds'] is None:
            # Classification tree
            color = list(self.colors['rgb'][np.argmax(value)])
            sorted_values = sorted(value, reverse=True)
            if len(sorted_values) == 1:
                alpha = 0
            else:
                alpha = ((sorted_values[0] - sorted_values[1])
                         / (1 - sorted_values[1]))
        else:
            # Regression tree or multi-output
            color = list(self.colors['rgb'][0])
            alpha = ((value - self.colors['bounds'][0]) /
                     (self.colors['bounds'][1] - self.colors['bounds'][0]))
        # unpack numpy scalars
        alpha = float(alpha)
        # compute the color as alpha against white
        color = [int(round(alpha * c + (1 - alpha) * 255, 0)) for c in color]
        # Return html color code in #RRGGBB format
        hex_codes = [str(i) for i in range(10)]
        hex_codes.extend(['a', 'b', 'c', 'd', 'e', 'f'])
        color = [hex_codes[c // 16] + hex_codes[c % 16] for c in color]

        return '#' + ''.join(color)

    def get_fill_color(self, tree, node_id):
        # Fetch appropriate color for node
        if 'rgb' not in self.colors:
            # Initialize colors and bounds if required
            self.colors['rgb'] = _color_brew(tree.n_classes[0])
            if tree.n_outputs != 1:
                # Find max and min impurities for multi-output
                self.colors['bounds'] = (np.min(-tree.impurity),
                                         np.max(-tree.impurity))
            elif (tree.n_classes[0] == 1 and
                  len(np.unique(tree.value)) != 1):
                # Find max and min values in leaf nodes for regression
                self.colors['bounds'] = (np.min(tree.value),
                                         np.max(tree.value))
        if tree.n_outputs == 1:
            node_val = (tree.value[node_id][0, :] /
                        tree.weighted_n_node_samples[node_id])
            if tree.n_classes[0] == 1:
                # Regression
                node_val = tree.value[node_id][0, :]
        else:
            # If multi-output color node by impurity
            node_val = -tree.impurity[node_id]
        return self.get_color(node_val)

    def node_to_str(self, tree, node_id, criterion):
        # Generate the node content string
        if tree.n_outputs == 1:
            value = tree.value[node_id][0, :]
        else:
            value = tree.value[node_id]

        # Should labels be shown?
        labels = (self.label == 'root' and node_id == 0) or self.label == 'all'

        characters = self.characters
        node_string = characters[-1]

        # Write node ID
        if self.node_ids:
            if labels:
                node_string += 'node '
            node_string += characters[0] + str(node_id) + characters[4]

        # Write decision criteria
        if tree.children_left[node_id] != _tree.TREE_LEAF:
            # Always write node decision criteria, except for leaves
            if self.feature_names is not None:
                feature = self.feature_names[tree.feature[node_id]]
            else:
                feature = "X%s%s%s" % (characters[1],
                                       tree.feature[node_id],
                                       characters[2])
            node_string += '%s %s %s%s' % (feature,
                                           characters[3],
                                           round(tree.threshold[node_id],
                                                 self.precision),
                                           characters[4])

        # Write impurity
        if self.impurity:
            if isinstance(criterion, _criterion.FriedmanMSE):
                criterion = "friedman_mse"
            elif not isinstance(criterion, six.string_types):
                criterion = "impurity"
            if labels:
                node_string += '%s = ' % criterion
            node_string += (str(round(tree.impurity[node_id], self.precision))
                            + characters[4])

        # Write node sample count
        if labels:
            node_string += 'samples = '
        if self.proportion:
            percent = (100. * tree.n_node_samples[node_id] /
                       float(tree.n_node_samples[0]))
            node_string += (str(round(percent, 1)) + '%' +
                            characters[4])
        else:
            node_string += (str(tree.n_node_samples[node_id]) +
                            characters[4])

        # Write node class distribution / regression value
        if self.proportion and tree.n_classes[0] != 1:
            # For classification this will show the proportion of samples
            value = value / tree.weighted_n_node_samples[node_id]
        if labels:
            node_string += 'value = '
        if tree.n_classes[0] == 1:
            # Regression
            value_text = np.around(value, self.precision)
        elif self.proportion:
            # Classification
            value_text = np.around(value, self.precision)
        elif np.all(np.equal(np.mod(value, 1), 0)):
            # Classification without floating-point weights
            value_text = value.astype(int)
        else:
            # Classification with floating-point weights
            value_text = np.around(value, self.precision)
        # Strip whitespace
        value_text = str(value_text.astype('S32')).replace("b'", "'")
        value_text = value_text.replace("' '", ", ").replace("'", "")
        if tree.n_classes[0] == 1 and tree.n_outputs == 1:
            value_text = value_text.replace("[", "").replace("]", "")
        value_text = value_text.replace("\n ", characters[4])
        node_string += value_text + characters[4]

        # Write node majority class
        if (self.class_names is not None and
                tree.n_classes[0] != 1 and
                tree.n_outputs == 1):
            # Only done for single-output classification trees
            if labels:
                node_string += 'class = '
            if self.class_names is not True:
                class_name = self.class_names[np.argmax(value)]
            else:
                class_name = "y%s%s%s" % (characters[1],
                                          np.argmax(value),
                                          characters[2])
            node_string += class_name

        # Clean up any trailing newlines
        if node_string.endswith(characters[4]):
            node_string = node_string[:-len(characters[4])]

        return node_string + characters[5]


class _DOTTreeExporter(_BaseTreeExporter):
    def __init__(self, out_file=SENTINEL, max_depth=None,
                 feature_names=None, class_names=None, label='all',
                 filled=False, leaves_parallel=False, impurity=True,
                 node_ids=False, proportion=False, rotate=False, rounded=False,
                 special_characters=False, precision=3):
        self.out_file = out_file
        self.max_depth = max_depth
        self.feature_names = feature_names
        self.class_names = class_names
        self.label = label
        self.filled = filled
        self.leaves_parallel = leaves_parallel
        self.impurity = impurity
        self.node_ids = node_ids
        self.proportion = proportion
        self.rotate = rotate
        self.rounded = rounded
        self.special_characters = special_characters
        self.precision = precision

        # PostScript compatibility for special characters
        if special_characters:
            self.characters = ['&#35;', '<SUB>', '</SUB>', '&le;', '<br/>',
                               '>', '<']
        else:
            self.characters = ['#', '[', ']', '<=', '\\n', '"', '"']

        # validate
        if isinstance(precision, Integral):
            if precision < 0:
                raise ValueError("'precision' should be greater or equal to 0."
                                 " Got {} instead.".format(precision))
        else:
            raise ValueError("'precision' should be an integer. Got {}"
                             " instead.".format(type(precision)))

        # The depth of each node for plotting with 'leaf' option
        self.ranks = {'leaves': []}
        # The colors to render each node with
        self.colors = {'bounds': None}

    def export(self, decision_tree):
        # Check length of feature_names before getting into the tree node
        # Raise error if length of feature_names does not match
        # n_features_ in the decision_tree
        if self.feature_names is not None:
            if len(self.feature_names) != decision_tree.n_features_:
                raise ValueError("Length of feature_names, %d "
                                 "does not match number of features, %d"
                                 % (len(self.feature_names),
                                    decision_tree.n_features_))
        # each part writes to out_file
        self.head()
        # Now recurse the tree and add node & edge attributes
        if isinstance(decision_tree, _tree.Tree):
            self.recurse(decision_tree, 0, criterion="impurity")
        else:
            self.recurse(decision_tree.tree_, 0,
                         criterion=decision_tree.criterion)

        self.tail()

    def tail(self):
        # If required, draw leaf nodes at same depth as each other
        if self.leaves_parallel:
            for rank in sorted(self.ranks):
                self.out_file.write(
                    "{rank=same ; " +
                    "; ".join(r for r in self.ranks[rank]) + "} ;\n")
        self.out_file.write("}")

    def head(self):
        self.out_file.write('digraph Tree {\n')

        # Specify node aesthetics
        self.out_file.write('node [shape=box')
        rounded_filled = []
        if self.filled:
            rounded_filled.append('filled')
        if self.rounded:
            rounded_filled.append('rounded')
        if len(rounded_filled) > 0:
            self.out_file.write(
                ', style="%s", color="black"'
                % ", ".join(rounded_filled))
        if self.rounded:
            self.out_file.write(', fontname=helvetica')
        self.out_file.write('] ;\n')

        # Specify graph & edge aesthetics
        if self.leaves_parallel:
            self.out_file.write(
                'graph [ranksep=equally, splines=polyline] ;\n')
        if self.rounded:
            self.out_file.write('edge [fontname=helvetica] ;\n')
        if self.rotate:
            self.out_file.write('rankdir=LR ;\n')

    def recurse(self, tree, node_id, criterion, parent=None, depth=0):
        if node_id == _tree.TREE_LEAF:
            raise ValueError("Invalid node_id %s" % _tree.TREE_LEAF)

        left_child = tree.children_left[node_id]
        right_child = tree.children_right[node_id]

        # Add node with description
        if self.max_depth is None or depth <= self.max_depth:

            # Collect ranks for 'leaf' option in plot_options
            if left_child == _tree.TREE_LEAF:
                self.ranks['leaves'].append(str(node_id))
            elif str(depth) not in self.ranks:
                self.ranks[str(depth)] = [str(node_id)]
            else:
                self.ranks[str(depth)].append(str(node_id))

            self.out_file.write(
                '%d [label=%s' % (node_id, self.node_to_str(tree, node_id,
                                                            criterion)))

            if self.filled:
                self.out_file.write(', fillcolor="%s"'
                                    % self.get_fill_color(tree, node_id))
            self.out_file.write('] ;\n')

            if parent is not None:
                # Add edge to parent
                self.out_file.write('%d -> %d' % (parent, node_id))
                if parent == 0:
                    # Draw True/False labels if parent is root node
                    angles = np.array([45, -45]) * ((self.rotate - .5) * -2)
                    self.out_file.write(' [labeldistance=2.5, labelangle=')
                    if node_id == 1:
                        self.out_file.write('%d, headlabel="True"]' %
                                            angles[0])
                    else:
                        self.out_file.write('%d, headlabel="False"]' %
                                            angles[1])
                self.out_file.write(' ;\n')

            if left_child != _tree.TREE_LEAF:
                self.recurse(tree, left_child, criterion=criterion,
                             parent=node_id, depth=depth + 1)
                self.recurse(tree, right_child, criterion=criterion,
                             parent=node_id, depth=depth + 1)

        else:
            self.ranks['leaves'].append(str(node_id))

            self.out_file.write('%d [label="(...)"' % node_id)
            if self.filled:
                # color cropped nodes grey
                self.out_file.write(', fillcolor="#C0C0C0"')
            self.out_file.write('] ;\n' % node_id)

            if parent is not None:
                # Add edge to parent
                self.out_file.write('%d -> %d ;\n' % (parent, node_id))


class _MPLTreeExporter(_BaseTreeExporter):
    def __init__(self, max_depth=None, feature_names=None,
                 class_names=None, label='all', filled=False,
                 leaves_parallel=False, impurity=True, node_ids=False,
                 proportion=False, rotate=False, rounded=False,
                 special_characters=False, precision=3, fontsize=None):
        self.max_depth = max_depth
        self.feature_names = feature_names
        self.class_names = class_names
        self.label = label
        self.filled = filled
        self.leaves_parallel = leaves_parallel
        self.impurity = impurity
        self.node_ids = node_ids
        self.proportion = proportion
        self.rotate = rotate
        self.rounded = rounded
        self.special_characters = special_characters
        self.precision = precision
        self.fontsize = fontsize

        # validate
        if isinstance(precision, Integral):
            if precision < 0:
                raise ValueError("'precision' should be greater or equal to 0."
                                 " Got {} instead.".format(precision))
        else:
            raise ValueError("'precision' should be an integer. Got {}"
                             " instead.".format(type(precision)))

        # The depth of each node for plotting with 'leaf' option
        self.ranks = {'leaves': []}
        # The colors to render each node with
        self.colors = {'bounds': None}

        self.characters = ['#', '[', ']', '<=', '\n', '', '']

        self.bbox_args = dict(fc='w')
        if self.rounded:
            self.bbox_args['boxstyle'] = "round"

        self.arrow_args = dict(arrowstyle="<-")

    def _make_tree(self, node_id, et):
        # traverses _tree.Tree recursively, builds intermediate
        # "_reingold_tilford.Tree" object
        name = self.node_to_str(et, node_id, criterion='entropy')
        if (et.children_left[node_id] != et.children_right[node_id]):
            children = [self._make_tree(et.children_left[node_id], et),
                        self._make_tree(et.children_right[node_id], et)]
        else:
            return Tree(name, node_id)
        return Tree(name, node_id, *children)

    def export(self, decision_tree, ax=None):
        import matplotlib.pyplot as plt
        from matplotlib.text import Annotation
        if ax is None:
            ax = plt.gca()
        ax.set_axis_off()
        my_tree = self._make_tree(0, decision_tree.tree_)
        dt = buchheim(my_tree)
        self.recurse(dt, decision_tree.tree_, ax)

        anns = [ann for ann in ax.get_children()
                if isinstance(ann, Annotation)]

        # get all the annotated points
        xys = [ann.xyann for ann in anns]

        # set axis limits with slight margin of .5
        mins = np.min(xys, axis=0)
        maxs = np.max(xys, axis=0) + .5

        ax.set_xlim(mins[0], maxs[0])
        ax.set_ylim(maxs[1], mins[1])

        if self.fontsize is None:
            # get figure to data transform
            inv = ax.transData.inverted()
            renderer = ax.figure.canvas.get_renderer()
            # update sizes of all bboxes
            for ann in anns:
                ann.update_bbox_position_size(renderer)
            # get max box width
            widths = [inv.get_matrix()[0, 0]
                      * ann.get_bbox_patch().get_window_extent().width
                      for ann in anns]
            # get minimum max size to not be too big.
            max_width = max(max(widths), 1)
            # adjust fontsize to avoid overlap
            # width should be around 1 in data coordinates
            size = anns[0].get_fontsize() / max_width
            for ann in anns:
                ann.set_fontsize(size)

    def recurse(self, node, tree, ax, depth=0):
        kwargs = dict(bbox=self.bbox_args, ha='center', va='center',
                      zorder=100 - 10 * depth)

        if self.fontsize is not None:
            kwargs['fontsize'] = self.fontsize

        xy = (node.x, node.y)

        if self.max_depth is None or depth <= self.max_depth:
            if self.filled:
                kwargs['bbox']['fc'] = self.get_fill_color(tree,
                                                           node.tree.node_id)
            if node.parent is None:
                # root
                ax.annotate(node.tree.node, xy, **kwargs)
            else:
                xy_parent = (node.parent.x, node.parent.y)
                kwargs["arrowprops"] = self.arrow_args
                ax.annotate(node.tree.node, xy_parent, xy, **kwargs)
            for child in node.children:
                self.recurse(child, tree, ax, depth=depth + 1)

        else:
            xy_parent = (node.parent.x, node.parent.y)
            kwargs["arrowprops"] = self.arrow_args
            kwargs['bbox']['fc'] = 'grey'
            ax.annotate("\n  (...)  \n", xy_parent, xy, **kwargs)


def export_graphviz(decision_tree, out_file=SENTINEL, max_depth=None,
                    feature_names=None, class_names=None, label='all',
                    filled=False, leaves_parallel=False, impurity=True,
                    node_ids=False, proportion=False, rotate=False,
                    rounded=False, special_characters=False, precision=3):
    """Export a decision tree in DOT format.

    This function generates a GraphViz representation of the decision tree,
    which is then written into `out_file`. Once exported, graphical renderings
    can be generated using, for example::

        $ dot -Tps tree.dot -o tree.ps      (PostScript format)
        $ dot -Tpng tree.dot -o tree.png    (PNG format)

    The sample counts that are shown are weighted with any sample_weights that
    might be present.

    Read more in the :ref:`User Guide <tree>`.

    Parameters
    ----------
    decision_tree : decision tree classifier
        The decision tree to be exported to GraphViz.

    out_file : file object or string, optional (default='tree.dot')
        Handle or name of the output file. If ``None``, the result is
        returned as a string. This will the default from version 0.20.

    max_depth : int, optional (default=None)
        The maximum depth of the representation. If None, the tree is fully
        generated.

    feature_names : list of strings, optional (default=None)
        Names of each of the features.

    class_names : list of strings, bool or None, optional (default=None)
        Names of each of the target classes in ascending numerical order.
        Only relevant for classification and not supported for multi-output.
        If ``True``, shows a symbolic representation of the class name.

    label : {'all', 'root', 'none'}, optional (default='all')
        Whether to show informative labels for impurity, etc.
        Options include 'all' to show at every node, 'root' to show only at
        the top root node, or 'none' to not show at any node.

    filled : bool, optional (default=False)
        When set to ``True``, paint nodes to indicate majority class for
        classification, extremity of values for regression, or purity of node
        for multi-output.

    leaves_parallel : bool, optional (default=False)
        When set to ``True``, draw all leaf nodes at the bottom of the tree.

    impurity : bool, optional (default=True)
        When set to ``True``, show the impurity at each node.

    node_ids : bool, optional (default=False)
        When set to ``True``, show the ID number on each node.

    proportion : bool, optional (default=False)
        When set to ``True``, change the display of 'values' and/or 'samples'
        to be proportions and percentages respectively.

    rotate : bool, optional (default=False)
        When set to ``True``, orient tree left to right rather than top-down.

    rounded : bool, optional (default=False)
        When set to ``True``, draw node boxes with rounded corners and use
        Helvetica fonts instead of Times-Roman.

    special_characters : bool, optional (default=False)
        When set to ``False``, ignore special characters for PostScript
        compatibility.

    precision : int, optional (default=3)
        Number of digits of precision for floating point in the values of
        impurity, threshold and value attributes of each node.

    Returns
    -------
    dot_data : string
        String representation of the input tree in GraphViz dot format.
        Only returned if ``out_file`` is None.

        .. versionadded:: 0.18

    Examples
    --------
    >>> from sklearn.datasets import load_iris
    >>> from sklearn import tree

    >>> clf = tree.DecisionTreeClassifier()
    >>> iris = load_iris()

    >>> clf = clf.fit(iris.data, iris.target)
    >>> tree.export_graphviz(clf,
    ...     out_file='tree.dot')                # doctest: +SKIP

    """

    check_is_fitted(decision_tree, 'tree_')
    own_file = False
    return_string = False
    try:
        if out_file == SENTINEL:
            warnings.warn("out_file can be set to None starting from 0.18. "
                          "This will be the default in 0.20.",
                          DeprecationWarning)
            out_file = "tree.dot"

        if isinstance(out_file, six.string_types):
            if six.PY3:
                out_file = open(out_file, "w", encoding="utf-8")
            else:
                out_file = open(out_file, "wb")
            own_file = True

        if out_file is None:
            return_string = True
            out_file = six.StringIO()

        exporter = _DOTTreeExporter(
            out_file=out_file, max_depth=max_depth,
            feature_names=feature_names, class_names=class_names, label=label,
            filled=filled, leaves_parallel=leaves_parallel, impurity=impurity,
            node_ids=node_ids, proportion=proportion, rotate=rotate,
            rounded=rounded, special_characters=special_characters,
            precision=precision)
        exporter.export(decision_tree)

        if return_string:
            return exporter.out_file.getvalue()

    finally:
        if own_file:
            out_file.close()

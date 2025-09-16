from __future__ import absolute_import
from __future__ import print_function
import ast as std_ast
import sys
from importlib import import_module
std_ast = import_module('ast')
import re
import os
import json
import tempfile
from pyverilog.vparser.parser import parse as pyverilog_parse
from pyverilog.vparser import ast as pyverilog_ast
from pyverilog.vparser.parser import ParseError
from networkx import DiGraph
import copy as cp
from typing import Dict, List, Tuple, Union, Optional, Any
from networkx.lazy_imports import _lazy_import
import hashlib
from datasketch import MinHash, MinHashLSH
import sys
import os
import json
import traceback
import locale
from io import StringIO
import xml.etree.ElementTree as ET
import matplotlib.pyplot as plt
import networkx as nx
import traceback
import locale

try:
    locale.setlocale(locale.LC_ALL, 'en_US.UTF-8')
except locale.Error:
    print("Warning: Could not set locale to en_US.UTF-8 or C")

class RTLFormatError(Exception):

    pass

class SyntaxError(Exception):

    pass


class Node(object):

    __slots__ = ['lineno', 'end_lineno', 'design_meta', 'attr_names', 'hash_value']

    def __init__(self, lineno=0, end_lineno=0):
        self.lineno = lineno
        self.end_lineno = end_lineno
        self.design_meta = {
            'is_rtl': True,
            'clock_group': 'default',
            'power_domain': 'default',
            'sensitivity': []
        }

        if not hasattr(self, 'attr_names'):
            self.attr_names = ()
        self.hash_value = None

    def children(self):

        return ()

    def compute_hash(self) -> str:

        if self.hash_value is not None:
            return self.hash_value

        hasher = hashlib.md5()
        hasher.update(self.__class__.__name__.encode())

        for child in self.children():
            if isinstance(child, Identifier):
                hasher.update(b"IDENTIFIER")
            else:
                if hasattr(child, 'compute_hash') and callable(child.compute_hash):
                    hasher.update(child.compute_hash().encode())
                else:
                    hasher.update(str(child.__class__.__name__).encode())

        self.hash_value = hasher.hexdigest()
        return self.hash_value

    def toplogic_tree_traverse(self, network_G: DiGraph, rvalue: bool = False, lvalue: bool = False, offset=0):

        indent = 2
        rnodes = []
        lnodes = []
        cnodes = []

        current_node_attrs = {}
        if self.attr_names:
            for attr_name_key in self.attr_names:
                current_node_attrs[attr_name_key] = getattr(self, attr_name_key, None)

        if hasattr(self, 'end_lineno') and self.end_lineno != 0:
            lines = (self.lineno, self.end_lineno)
        else:
            lines = (self.lineno, self.lineno)

        current_graph_node_name = None

        if isinstance(self, ModuleDef):
            current_graph_node_name = f"ModuleDef_{self.name}"
        elif isinstance(self, (Input, Output, Inout, Wire, Reg, Integer, Real, Identifier, Logic)):
            current_graph_node_name = self.name
        elif isinstance(self, (
                Assign, Always, AlwaysComb, AlwaysFF, AlwaysLatch, BlockingSubstitution, NonblockingSubstitution)):
            current_graph_node_name = f"{self.__class__.__name__}_L{lines[0]}_{id(self)}"
        elif isinstance(self, (
                IfStatement, CaseStatement, CasexStatement, CasezStatement, ForStatement, WhileStatement, Repeat, Block,
                UniqueCaseStatement)):
            current_graph_node_name = f"{self.__class__.__name__}_L{lines[0]}_{id(self)}"
        elif isinstance(self, Instance):
            current_graph_node_name = f"Instance_{self.name}({self.module})"
        elif isinstance(self, IntConst):
            current_graph_node_name = f"IntConst_{self.value}_L{lines[0]}_{id(self)}"
        elif isinstance(self, FloatConst):
            current_graph_node_name = f"FloatConst_{self.value}_L{lines[0]}_{id(self)}"
        elif isinstance(self, StringConst):
            current_graph_node_name = f"StringConst_{str(self.value)[:10]}_L{lines[0]}_{id(self)}"

        elif isinstance(self,
                        (Source, Description, Portlist, Paramlist, SensList, Width, Length, Dimensions, Value, Constant,
                         Variable, Tri, Genvar, Ioport, Parameter, Localparam, Supply, Decl, Concat, LConcat,
                         Partselect, Pointer,
                         Operator, UnaryOperator, Cond,
                         Substitution,
                         Case, Initial, EventStatement, WaitStatement, ForeverStatement, DelayStatement,
                         InstanceList, ParamArg, PortArg, Function, FunctionCall, Task, TaskCall,
                         GenerateStatement, SystemCall, IdentifierScopeLabel, IdentifierScope,
                         Pragma, PragmaEntry, Disable, ParallelBlock, SingleStatement, EmbeddedCode
                         )):

            base_name_part = self.name if hasattr(self, 'name') and isinstance(self.name, str) else \
                (self.value if hasattr(self, 'value') and isinstance(self.value, (str, int, float)) else "")
            if base_name_part:
                current_graph_node_name = f"{self.__class__.__name__}_{str(base_name_part)[:10]}_L{lines[0]}_{id(self)}"
            else:
                current_graph_node_name = f"{self.__class__.__name__}_L{lines[0]}_{id(self)}"


        else:
            current_graph_node_name = f"{self.__class__.__name__}_L{lines[0]}_{id(self)}"

        if current_graph_node_name and current_graph_node_name not in network_G:
            graph_node_attributes_for_networkx = {}
            for attr_name, attr_value in current_node_attrs.items():
                if isinstance(attr_value, (str, int, float, bool, type(None))):
                    graph_node_attributes_for_networkx[attr_name] = attr_value
                elif isinstance(attr_value, list):
                    try:
                        graph_node_attributes_for_networkx[attr_name] = ", ".join(map(str, attr_value))
                    except TypeError:
                        graph_node_attributes_for_networkx[attr_name] = str(attr_value)
                elif isinstance(attr_value, dict):
                    graph_node_attributes_for_networkx[attr_name] = json.dumps(attr_value, default=str)
                else:
                    graph_node_attributes_for_networkx[attr_name] = str(attr_value)
            graph_node_attributes_for_networkx.pop('type', None)
            network_G.add_node(
                current_graph_node_name,
                line=lines,
                type=self.__class__.__name__,
                **graph_node_attributes_for_networkx
            )
            cnodes.append(current_graph_node_name)

        if isinstance(self, Rvalue):
            rvalue = True
        if isinstance(self, Lvalue):
            lvalue = True

        for c in self.children():
            if c is None: continue
            child_nodes, child_rnodes, child_lnodes = c.toplogic_tree_traverse(network_G, rvalue, lvalue,
                                                                               offset + indent)
            cnodes.extend(child_nodes)
            rnodes.extend(child_rnodes)
            lnodes.extend(child_lnodes)

            if current_graph_node_name and child_nodes:
                for child_graph_name in child_nodes:
                    if current_graph_node_name != child_graph_name:
                        network_G.add_edge(current_graph_node_name, child_graph_name, lines=lines,
                                           type='structural_parent_child')

        if isinstance(self, (
                AlwaysComb, Always, Assign, AlwaysFF, AlwaysLatch, BlockingSubstitution, NonblockingSubstitution)):
            if isinstance(self, Assign) or isinstance(self, (BlockingSubstitution, NonblockingSubstitution)):
                if self.left and hasattr(self.left, 'var') and isinstance(self.left.var,
                                                                          Node):

                    l_target_name_node = self.left.var
                    if isinstance(l_target_name_node, Identifier):
                        l_target_name = l_target_name_node.name
                    else:
                        l_target_name = f"{l_target_name_node.__class__.__name__}_{str(l_target_name_node)}_{id(l_target_name_node)}"

                    if l_target_name not in network_G:
                        network_G.add_node(l_target_name, type=l_target_name_node.__class__.__name__,
                                           lineno=self.left.lineno)

                    source_identifiers = []

                    def find_identifiers_in_expr(expr_node):
                        if expr_node is None: return
                        if isinstance(expr_node, Identifier):
                            source_identifiers.append(expr_node.name)

                        elif hasattr(expr_node, 'children') and not isinstance(expr_node, (
                                Identifier, IntConst, FloatConst, StringConst)):
                            for child_of_expr in expr_node.children():
                                find_identifiers_in_expr(child_of_expr)


                    actual_right_expr_node = None
                    if isinstance(self.right, Rvalue) and self.right.var:
                        actual_right_expr_node = self.right.var
                    elif isinstance(self.right,
                                    Node):
                        actual_right_expr_node = self.right

                    if actual_right_expr_node:
                        find_identifiers_in_expr(actual_right_expr_node)

                    for src_name in set(source_identifiers):
                        if src_name not in network_G:
                            right_lineno = self.right.lineno if hasattr(self.right, 'lineno') else self.lineno
                            network_G.add_node(src_name, type='Signal', lineno=right_lineno)
                        if src_name != l_target_name:
                            network_G.add_edge(src_name, l_target_name, lines=lines, type='data_flow',
                                               assign_type=self.__class__.__name__)

            elif isinstance(self, (Always, AlwaysComb, AlwaysFF, AlwaysLatch)):
                block_node_name = current_graph_node_name
                unique_rnodes = set(r for r in rnodes if r is not None and r != block_node_name)
                unique_lnodes = set(l for l in lnodes if l is not None and l != block_node_name)

                for r_sig_name in unique_rnodes:
                    if r_sig_name not in network_G:
                        network_G.add_node(r_sig_name, type='Signal', lineno=self.lineno)
                    network_G.add_edge(r_sig_name, block_node_name, lines=lines, type='input_to_block')

                for l_sig_name in unique_lnodes:
                    if l_sig_name not in network_G:
                        network_G.add_node(l_sig_name, type='Signal', lineno=self.lineno)
                    network_G.add_edge(block_node_name, l_sig_name, lines=lines, type='output_from_block')

        if isinstance(self, (
        CaseStatement, IfStatement, CasexStatement, CasezStatement, UniqueCaseStatement)):
            control_node_name = None
            cond_expr_node = None
            if isinstance(self, IfStatement):
                cond_expr_node = self.cond
            elif isinstance(self, (
            CaseStatement, CasexStatement, CasezStatement, UniqueCaseStatement)):
                cond_expr_node = self.comp

            if cond_expr_node:
                if isinstance(cond_expr_node, Identifier):
                    control_node_name = cond_expr_node.name
                elif isinstance(cond_expr_node, (IntConst, FloatConst, StringConst)):
                    control_node_name = f"{cond_expr_node.__class__.__name__}_{cond_expr_node.value}_L{cond_expr_node.lineno}"
                else:
                    control_node_name = f"ConditionExpr_L{cond_expr_node.lineno}_{id(cond_expr_node)}"
                    if control_node_name not in network_G:
                        network_G.add_node(control_node_name, type=cond_expr_node.__class__.__name__,
                                           line=(cond_expr_node.lineno,
                                                 getattr(cond_expr_node, 'end_lineno', cond_expr_node.lineno)))

            controlled_statements = []
            if isinstance(self, IfStatement):
                if self.true_statement: controlled_statements.append(self.true_statement)
                if self.false_statement: controlled_statements.append(self.false_statement)
            elif isinstance(self, (CaseStatement, CasexStatement, CasezStatement,
                                   UniqueCaseStatement)) and self.caselist:
                for case_item_node in self.caselist:
                    if case_item_node and case_item_node.statement:
                        controlled_statements.append(case_item_node.statement)

            for stmt_node in controlled_statements:
                if stmt_node is None: continue
                stmt_graph_node_name = f"{stmt_node.__class__.__name__}_L{stmt_node.lineno}_{id(stmt_node)}"
                if stmt_graph_node_name not in network_G:
                    network_G.add_node(stmt_graph_node_name, type=stmt_node.__class__.__name__,
                                       line=(stmt_node.lineno, getattr(stmt_node, 'end_lineno', stmt_node.lineno)))

                if control_node_name and stmt_graph_node_name:
                    if control_node_name != stmt_graph_node_name:
                        network_G.add_edge(control_node_name, stmt_graph_node_name, lines=lines, type='control_flow')

        return [n for n in [current_graph_node_name] if n is not None], \
            [r for r in cp.deepcopy(rnodes) if r is not None], \
            [l for l in cp.deepcopy(lnodes) if l is not None]

    def show(self, buf=sys.stdout, offset=0, attrnames=False, showlineno=True):
        indent = 2
        lead = ' ' * offset
        buf.write(lead + self.__class__.__name__ + ': ')

        if self.attr_names:
            attrs_to_display = []
            for n in self.attr_names:
                if hasattr(self, n):
                    val = getattr(self, n)
                    if isinstance(val, Node):
                        val_repr = f"Node:{val.__class__.__name__}"
                        if hasattr(val, 'name'):
                            val_repr += f"({val.name})"
                        elif hasattr(val, 'value'):
                            val_repr += f"({val.value})"
                    elif isinstance(val, list) and len(val) > 5:
                        val_repr = f"[List with {len(val)} items]"
                    else:
                        val_repr = str(val)
                    attrs_to_display.append((n, val_repr))
                else:
                    attrs_to_display.append((n, "N/A (Attr missing)"))

            if attrnames:
                attrstr = ', '.join('%s=%s' % (n_disp, v_disp) for (n_disp, v_disp) in attrs_to_display)
            else:
                attrstr = ', '.join('%s' % v_disp for (_, v_disp) in attrs_to_display)
            buf.write(attrstr)

        if showlineno:

            if hasattr(self, 'end_lineno') and self.end_lineno != 0 and self.end_lineno != self.lineno:
                buf.write(' (from %s to %s)' % (self.lineno, self.end_lineno))
            else:
                buf.write(' (at %s)' % self.lineno)
        buf.write('\n')

        for c in self.children():
            if c is not None:
                c.show(buf, offset + indent, attrnames, showlineno)

    def __eq__(self, other):
        if type(self) != type(other):
            return False


        self_attr_names = self.attr_names if hasattr(self, 'attr_names') else ()
        other_attr_names = other.attr_names if hasattr(other, 'attr_names') else ()

        if self_attr_names != other_attr_names:

            pass

        self_attrs_values = tuple([getattr(self, a, None) for a in self_attr_names])
        other_attrs_values = tuple(
            [getattr(other, a, None) for a in self_attr_names])

        if self_attrs_values != other_attrs_values:
            return False

        self_children = self.children() if self.children() is not None else ()
        other_children_list = other.children() if other.children() is not None else ()

        if len(self_children) != len(other_children_list):
            return False

        for i, c in enumerate(self_children):
            if c != other_children_list[i]:
                return False
        return True

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        current_attr_names = self.attr_names if hasattr(self, 'attr_names') else ()
        attr_values = []
        for a in current_attr_names:
            attr_values.append(getattr(self, a, None))

        s = hash(tuple(attr_values))
        children_to_hash = self.children()
        if children_to_hash is None:
            c_hash = hash(None)
        else:
            try:

                children_tuple = []
                for child in children_to_hash:
                    if child is None:
                        children_tuple.append(None)

                    elif hasattr(child, '__hash__'):
                        children_tuple.append(child)
                    else:
                        children_tuple.append(id(child))

                c_hash = hash(tuple(children_tuple))
            except TypeError:
                c_hash = hash(tuple(id(child) for child in children_to_hash if child is not None))
        return hash((self.__class__.__name__, s, c_hash))

    def to_dict(self) -> Dict[str, Any]:
        node_dict = {
            "type": self.__class__.__name__,
            "lineno": self.lineno,
        }
        if hasattr(self, 'end_lineno') and self.end_lineno != 0:
            node_dict["end_lineno"] = self.end_lineno


        current_attr_names = self.attr_names if hasattr(self, 'attr_names') else ()
        if current_attr_names:
            attrs = {}
            for attr_name in current_attr_names:
                value = getattr(self, attr_name, None)
                if isinstance(value, Node):

                    attrs[attr_name] = f"NodeRef:{value.__class__.__name__}"
                    if hasattr(value, 'name'):
                        attrs[attr_name] += f"({value.name})"
                    elif hasattr(value, 'value'):
                        attrs[attr_name] += f"({value.value})"
                elif isinstance(value, (list, tuple)):
                    serializable_list = []
                    for item in value:
                        if isinstance(item, Node):
                            serializable_list.append(item.to_dict())
                        elif not isinstance(item, (dict, list, str, int, float, bool, type(None))):
                            serializable_list.append(str(item))
                        else:
                            serializable_list.append(item)
                    attrs[attr_name] = serializable_list
                elif not isinstance(value, (dict, list, str, int, float, bool, type(None))):
                    attrs[attr_name] = str(value)
                else:
                    attrs[attr_name] = value
            if attrs: node_dict["attributes"] = attrs

        children_list = []
        children_iterable = self.children()
        if children_iterable is None: children_iterable = ()

        for child in children_iterable:
            if child is not None and hasattr(child, 'to_dict'):
                children_list.append(child.to_dict())
            elif child is not None:
                children_list.append(
                    {"type": child.__class__.__name__,
                     "value": str(child),
                     "lineno": getattr(child, 'lineno', 0)})
        if children_list:
            node_dict["children"] = children_list
        return node_dict


class Source(Node):
    attr_names = ('name',)

    def __init__(self, name, description, lineno=0):
        super().__init__(lineno)
        self.name = name
        self.description = description

    def children(self):
        nodelist = []
        if self.description:
            nodelist.append(self.description)
        return tuple(nodelist)


class Description(Node):
    attr_names = ()

    def __init__(self, definitions, lineno=0):
        super().__init__(lineno)
        self.definitions = definitions

    def children(self):
        nodelist = []
        if self.definitions:
            nodelist.extend(self.definitions)
        return tuple(nodelist)


class ModuleDef(Node):
    attr_names = ('name', 'default_nettype')

    def __init__(self, name, paramlist, portlist, items, default_nettype='wire', lineno=0, end_lineno=0):
        super().__init__(lineno, end_lineno)
        self.name = name
        self.paramlist = paramlist
        self.portlist = portlist
        self.items = items
        self.default_nettype = default_nettype
        self.design_meta.update({'power_domains': ['default'], 'clock_crossing': []})

    def children(self):
        nodelist = []
        if self.paramlist:
            nodelist.append(self.paramlist)
        if self.portlist:
            nodelist.append(self.portlist)
        if self.items:
            nodelist.extend(self.items)
        return tuple(nodelist)


class Paramlist(Node):
    attr_names = ()

    def __init__(self, params, lineno=0):
        super().__init__(lineno)
        self.params = params

    def children(self):
        nodelist = []
        if self.params:
            nodelist.extend(self.params)
        return tuple(nodelist)


class Portlist(Node):
    attr_names = ()

    def __init__(self, ports, lineno=0):
        super().__init__(lineno)
        self.ports = ports

    def children(self):
        nodelist = []
        if self.ports:
            nodelist.extend(self.ports)
        return tuple(nodelist)


class Port(Node):
    attr_names = ('name', 'type',)

    def __init__(self, name, width, dimensions, type, lineno=0):
        super().__init__(lineno)
        self.name = name
        self.width = width
        self.dimensions = dimensions
        self.type = type
        self.design_meta.update({'port_direction': type})

    def children(self):
        nodelist = []
        if self.width:
            nodelist.append(self.width)
        if self.dimensions and isinstance(self.dimensions, Node):
            nodelist.append(self.dimensions)
        elif isinstance(self.dimensions, list):
            nodelist.extend(d for d in self.dimensions if isinstance(d, Node))
        return tuple(nodelist)


class Width(Node):
    attr_names = ()

    def __init__(self, msb, lsb, lineno=0):
        super().__init__(lineno)
        self.msb = msb
        self.lsb = lsb

    def children(self):
        nodelist = []
        if self.msb and isinstance(self.msb, Node):
            nodelist.append(self.msb)
        if self.lsb and isinstance(self.lsb, Node):
            nodelist.append(self.lsb)
        return tuple(nodelist)


class Length(Width):
    pass


class Dimensions(Node):
    attr_names = ()

    def __init__(self, lengths, lineno=0):
        super().__init__(lineno)
        self.lengths = lengths

    def children(self):
        nodelist = []
        if self.lengths:
            nodelist.extend(l for l in self.lengths if isinstance(l, Node))
        return tuple(nodelist)


class Identifier(Node):
    attr_names = ('name', 'scope')

    def __init__(self, name, scope=None, lineno=0):
        super().__init__(lineno)
        self.name = name
        self.scope = scope

    def children(self):
        nodelist = []
        if self.scope and isinstance(self.scope, Node):
            nodelist.append(self.scope)
        return tuple(nodelist)

    def __repr__(self):
        if self.scope is None:
            return self.name
        return self.scope.__repr__() + '.' + self.name


class Value(Node):
    attr_names = ()

    def __init__(self, value, lineno=0):
        super().__init__(lineno)
        self.value = value

    def children(self):
        nodelist = []
        if self.value and isinstance(self.value, Node):
            nodelist.append(self.value)
        return tuple(nodelist)


class Constant(Value):
    attr_names = ('value',)

    def __init__(self, value, lineno=0):
        super().__init__(lineno)

        self.value = value

    def children(self):
        return tuple()

    def __repr__(self):
        return str(self.value)


class IntConst(Constant):

    attr_names = Constant.attr_names + ('base',)

    def __init__(self, value, base=10, lineno=0):
        super().__init__(value, lineno)
        self.base = base


class FloatConst(Constant):
    pass


class StringConst(Constant):
    pass


class Variable(Value):
    attr_names = ('name', 'signed')

    def __init__(self, name, width=None, signed=False, dimensions=None, value=None, lineno=0):

        super().__init__(value, lineno)
        self.name = name
        self.width = width
        self.signed = signed
        self.dimensions = dimensions

    def children(self):
        nodelist = []
        if self.width and isinstance(self.width, Node):
            nodelist.append(self.width)
        if self.dimensions and isinstance(self.dimensions, Node):
            nodelist.append(self.dimensions)

        nodelist.extend(super().children())
        return tuple(nodelist)


class Input(Variable):

    def __init__(self, name, width=None, signed=False, dimensions=None, value=None, lineno=0):
        super().__init__(name, width, signed, dimensions, value, lineno)
        self.design_meta.update({'port_type': 'rtl_input', 'port_direction': 'input'})


class Output(Variable):
    def __init__(self, name, width=None, signed=False, dimensions=None, value=None, lineno=0):
        super().__init__(name, width, signed, dimensions, value, lineno)
        self.design_meta.update({'port_type': 'rtl_output', 'driven_by': [], 'port_direction': 'output'})


class Inout(Variable):
    def __init__(self, name, width=None, signed=False, dimensions=None, value=None, lineno=0):
        super().__init__(name, width, signed, dimensions, value, lineno)
        self.design_meta.update({'port_type': 'rtl_inout', 'bidirectional': True, 'port_direction': 'inout'})


class Tri(Variable):
    pass


class Wire(Variable):
    pass


class Reg(Variable):
    pass


class Integer(Variable):
    pass


class Real(Variable):
    pass


class Genvar(Variable):
    pass


class Ioport(Node):
    attr_names = ()

    def __init__(self, first, second=None, lineno=0):
        super().__init__(lineno)
        self.first = first
        self.second = second

    def children(self):
        nodelist = []
        if self.first and isinstance(self.first, Node):
            nodelist.append(self.first)
        if self.second and isinstance(self.second, Node):
            nodelist.append(self.second)
        return tuple(nodelist)


class Parameter(Node):
    attr_names = ('name', 'signed')

    def __init__(self, name, value, width=None, signed=False, lineno=0):
        super().__init__(lineno)
        self.name = name
        self.value = value
        self.width = width
        self.signed = signed
        self.dimensions = None

    def children(self):
        nodelist = []
        if self.value and isinstance(self.value, Node):
            nodelist.append(self.value)
        if self.width and isinstance(self.width, Node):
            nodelist.append(self.width)
        return tuple(nodelist)


class Localparam(Parameter):
    pass


class Supply(Parameter):
    pass


class Decl(Node):
    attr_names = ()

    def __init__(self, list_of_vars, lineno=0):
        super().__init__(lineno)
        self.list = list_of_vars

    def children(self):
        nodelist = []
        if self.list:
            nodelist.extend(v for v in self.list if isinstance(v, Node))
        return tuple(nodelist)


class Concat(Node):
    attr_names = ()

    def __init__(self, list_of_exprs, lineno=0):
        super().__init__(lineno)
        self.list = list_of_exprs

    def children(self):
        nodelist = []
        if self.list:
            nodelist.extend(e for e in self.list if isinstance(e, Node))
        return tuple(nodelist)


class LConcat(Concat):
    pass


class Repeat(Node):
    attr_names = ()

    def __init__(self, value, times, lineno=0):
        super().__init__(lineno)
        self.value = value
        self.times = times

    def children(self):
        nodelist = []
        if self.value and isinstance(self.value, Node):
            nodelist.append(self.value)
        if self.times and isinstance(self.times, Node):
            nodelist.append(self.times)
        return tuple(nodelist)


class Partselect(Node):
    attr_names = ()

    def __init__(self, var, msb, lsb, lineno=0):
        super().__init__(lineno)
        self.var = var
        self.msb = msb
        self.lsb = lsb

    def children(self):
        nodelist = []
        if self.var and isinstance(self.var, Node):
            nodelist.append(self.var)
        if self.msb and isinstance(self.msb, Node):
            nodelist.append(self.msb)
        if self.lsb and isinstance(self.lsb, Node):
            nodelist.append(self.lsb)
        return tuple(nodelist)


class Pointer(Node):
    attr_names = ()

    def __init__(self, var, ptr, lineno=0):
        super().__init__(lineno)
        self.var = var
        self.ptr = ptr

    def children(self):
        nodelist = []
        if self.var and isinstance(self.var, Node):
            nodelist.append(self.var)
        if self.ptr and isinstance(self.ptr, Node):
            nodelist.append(self.ptr)
        return tuple(nodelist)


class Lvalue(Node):
    attr_names = ()

    def __init__(self, var, lineno=0):
        super().__init__(lineno)
        self.var = var

    def children(self):
        nodelist = []
        if self.var and isinstance(self.var, Node):
            nodelist.append(self.var)
        return tuple(nodelist)


class Rvalue(Node):
    attr_names = ()

    def __init__(self, var, lineno=0):
        super().__init__(lineno)
        self.var = var

    def children(self):
        nodelist = []
        if self.var and isinstance(self.var, Node):
            nodelist.append(self.var)
        return tuple(nodelist)



class Operator(Node):
    attr_names = ()

    def __init__(self, left, right, lineno=0):
        super().__init__(lineno)
        self.left = left
        self.right = right

    def children(self):
        nodelist = []
        if self.left and isinstance(self.left, Node): nodelist.append(self.left)
        if self.right and isinstance(self.right, Node): nodelist.append(self.right)
        return tuple(nodelist)

    def __repr__(self):
        ret = '(' + self.__class__.__name__
        for c_node in self.children():
            ret += ' ' + c_node.__repr__()
        ret += ')'
        return ret


class UnaryOperator(Operator):
    attr_names = ()

    def __init__(self, right, lineno=0):
        super().__init__(None, right, lineno)

    def children(self):
        nodelist = []
        if self.right and isinstance(self.right, Node):
            nodelist.append(self.right)
        return tuple(nodelist)



class Uplus(UnaryOperator): pass


class Uminus(UnaryOperator): pass


class Ulnot(UnaryOperator): pass


class Unot(UnaryOperator): pass


class Uand(UnaryOperator): pass


class Unand(UnaryOperator): pass


class Uor(UnaryOperator): pass


class Unor(UnaryOperator): pass


class Uxor(UnaryOperator): pass


class Uxnor(UnaryOperator): pass



class Power(Operator): pass


class Times(Operator): pass


class Divide(Operator): pass


class Mod(Operator): pass



class Plus(Operator): pass


class Minus(Operator): pass



class Sll(Operator): pass


class Srl(Operator): pass


class Sla(Operator): pass


class Sra(Operator): pass



class LessThan(Operator): pass


class GreaterThan(Operator): pass


class LessEq(Operator): pass


class GreaterEq(Operator): pass



class Eq(Operator): pass


class NotEq(Operator): pass


class Eql(Operator): pass


class NotEql(Operator): pass



class And(Operator): pass


class Xor(Operator): pass


class Xnor(Operator): pass



class Or(Operator): pass



class Land(Operator): pass



class Lor(Operator): pass



class Cond(Operator):
    attr_names = ()

    def __init__(self, cond, true_value, false_value, lineno=0):

        Node.__init__(self, lineno)
        self.cond = cond
        self.true_value = true_value
        self.false_value = false_value

    def children(self):
        nodelist = []
        if self.cond and isinstance(self.cond, Node): nodelist.append(self.cond)
        if self.true_value and isinstance(self.true_value, Node): nodelist.append(self.true_value)
        if self.false_value and isinstance(self.false_value, Node): nodelist.append(self.false_value)
        return tuple(nodelist)


class Assign(Node):
    attr_names = ()

    def __init__(self, left, right, ldelay=None, rdelay=None, lineno=0):
        super().__init__(lineno)
        self.left = left
        self.right = right
        self.ldelay = ldelay
        self.rdelay = rdelay

    def children(self):
        nodelist = []
        if self.left and isinstance(self.left, Node): nodelist.append(self.left)
        if self.right and isinstance(self.right, Node): nodelist.append(self.right)
        if self.ldelay and isinstance(self.ldelay, Node): nodelist.append(self.ldelay)
        if self.rdelay and isinstance(self.rdelay, Node): nodelist.append(self.rdelay)
        return tuple(nodelist)


class Always(Node):
    attr_names = ()

    def __init__(self, sens_list, statement, lineno=0):
        super().__init__(lineno)
        self.sens_list = sens_list
        self.statement = statement
        self.design_meta.update({'type': 'unknown', 'signals': []})

    def children(self):
        nodelist = []
        if self.sens_list and isinstance(self.sens_list, Node): nodelist.append(self.sens_list)
        if self.statement and isinstance(self.statement, Node): nodelist.append(self.statement)
        return tuple(nodelist)



class AlwaysFF(Always):
    def __init__(self, sens_list, statement, lineno=0):
        super().__init__(sens_list, statement, lineno)
        self.design_meta.update({'type': 'sequential', 'has_clock': None, 'has_reset': None})


class AlwaysComb(Always):
    def __init__(self, sens_list, statement, lineno=0):
        super().__init__(sens_list, statement, lineno)
        self.design_meta.update({'type': 'combinational', 'driven_signals': []})

class AlwaysLatch(Always):
    def __init__(self, sens_list, statement, lineno=0):
        super().__init__(sens_list, statement, lineno)
        self.design_meta.update(
            {'type': 'latch', 'latch_signals': [],
             'warning': 'Latches may indicate incomplete logic'})


class SensList(Node):
    attr_names = ()

    def __init__(self, list_of_sens, lineno=0):
        super().__init__(lineno)
        self.list = list_of_sens

    def children(self):
        nodelist = []
        if self.list:
            nodelist.extend(s for s in self.list if isinstance(s, Node))
        return tuple(nodelist)


class Sens(Node):
    attr_names = ('type',)

    def __init__(self, sig, type='posedge', lineno=0):
        super().__init__(lineno)
        self.sig = sig
        self.type = type

    def children(self):
        nodelist = []
        if self.sig and isinstance(self.sig, Node):
            nodelist.append(self.sig)
        return tuple(nodelist)


class Substitution(Node):
    attr_names = ()

    def __init__(self, left, right, ldelay=None, rdelay=None, lineno=0):
        super().__init__(lineno)
        self.left = left
        self.right = right
        self.ldelay = ldelay
        self.rdelay = rdelay

    def children(self):
        nodelist = []
        if self.left and isinstance(self.left, Node): nodelist.append(self.left)
        if self.right and isinstance(self.right, Node): nodelist.append(self.right)
        if self.ldelay and isinstance(self.ldelay, Node): nodelist.append(self.ldelay)
        if self.rdelay and isinstance(self.rdelay, Node): nodelist.append(self.rdelay)
        return tuple(nodelist)


class BlockingSubstitution(Substitution):
    def __init__(self, left, right, ldelay=None, rdelay=None, lineno=0):
        super().__init__(left, right, ldelay, rdelay, lineno)
        self.blocking = True


class NonblockingSubstitution(Substitution):
    def __init__(self, left, right, ldelay=None, rdelay=None, lineno=0):
        super().__init__(left, right, ldelay, rdelay, lineno)
        self.blocking = False


class IfStatement(Node):
    attr_names = ()

    def __init__(self, cond, true_statement, false_statement, lineno=0):
        super().__init__(lineno)
        self.cond = cond
        self.true_statement = true_statement
        self.false_statement = false_statement

    def children(self):
        nodelist = []
        if self.cond and isinstance(self.cond, Node): nodelist.append(self.cond)
        if self.true_statement and isinstance(self.true_statement, Node): nodelist.append(self.true_statement)
        if self.false_statement and isinstance(self.false_statement, Node): nodelist.append(self.false_statement)
        return tuple(nodelist)


class ForStatement(Node):
    attr_names = ()

    def __init__(self, pre, cond, post, statement, lineno=0):
        super().__init__(lineno)
        self.pre = pre
        self.cond = cond
        self.post = post
        self.statement = statement

    def children(self):
        nodelist = []
        if self.pre and isinstance(self.pre, Node): nodelist.append(self.pre)
        if self.cond and isinstance(self.cond, Node): nodelist.append(self.cond)
        if self.post and isinstance(self.post, Node): nodelist.append(self.post)
        if self.statement and isinstance(self.statement, Node): nodelist.append(self.statement)
        return tuple(nodelist)


class WhileStatement(Node):
    attr_names = ()

    def __init__(self, cond, statement, lineno=0):
        super().__init__(lineno)
        self.cond = cond
        self.statement = statement

    def children(self):
        nodelist = []
        if self.cond and isinstance(self.cond, Node): nodelist.append(self.cond)
        if self.statement and isinstance(self.statement, Node): nodelist.append(self.statement)
        return tuple(nodelist)


class CaseStatement(Node):
    attr_names = ()

    def __init__(self, comp, caselist, lineno=0):
        super().__init__(lineno)
        self.comp = comp
        self.caselist = caselist

    def children(self):
        nodelist = []
        if self.comp and isinstance(self.comp, Node): nodelist.append(self.comp)
        if self.caselist:
            nodelist.extend(c for c in self.caselist if isinstance(c, Node))
        return tuple(nodelist)


class CasexStatement(CaseStatement): pass


class CasezStatement(CaseStatement): pass


class UniqueCaseStatement(CaseStatement): pass


class Case(Node):
    attr_names = ()

    def __init__(self, cond, statement, lineno=0):
        super().__init__(lineno)
        self.cond = cond
        self.statement = statement

    def children(self):
        nodelist = []
        if isinstance(self.cond, list):
            for c_item in self.cond:
                if c_item and isinstance(c_item, Node): nodelist.append(c_item)
        elif self.cond and isinstance(self.cond, Node):
            nodelist.append(self.cond)
        if self.statement and isinstance(self.statement, Node): nodelist.append(self.statement)
        return tuple(nodelist)


class Block(Node):
    attr_names = ('scope',)

    def __init__(self, statements, scope=None, lineno=0):
        super().__init__(lineno)
        self.statements = statements
        self.scope = scope

    def children(self):
        nodelist = []
        if self.statements:
            nodelist.extend(s for s in self.statements if isinstance(s, Node))
        return tuple(nodelist)


class Initial(Node):
    attr_names = ()

    def __init__(self, statement, lineno=0):
        super().__init__(lineno)
        self.statement = statement

    def children(self):
        nodelist = []
        if self.statement and isinstance(self.statement, Node):
            nodelist.append(self.statement)
        return tuple(nodelist)


class EventStatement(Node):
    attr_names = ()

    def __init__(self, senslist_or_event_name, lineno=0):
        super().__init__(lineno)
        self.senslist = senslist_or_event_name

    def children(self):
        nodelist = []
        if self.senslist and isinstance(self.senslist, Node):
            nodelist.append(self.senslist)
        return tuple(nodelist)


class WaitStatement(Node):
    attr_names = ()

    def __init__(self, cond, statement, lineno=0):
        super().__init__(lineno)
        self.cond = cond
        self.statement = statement

    def children(self):
        nodelist = []
        if self.cond and isinstance(self.cond, Node): nodelist.append(self.cond)
        if self.statement and isinstance(self.statement, Node): nodelist.append(self.statement)
        return tuple(nodelist)


class ForeverStatement(Node):
    attr_names = ()

    def __init__(self, statement, lineno=0):
        super().__init__(lineno)
        self.statement = statement

    def children(self):
        nodelist = []
        if self.statement and isinstance(self.statement, Node):
            nodelist.append(self.statement)
        return tuple(nodelist)


class DelayStatement(Node):
    attr_names = ()

    def __init__(self, delay, lineno=0):
        super().__init__(lineno)
        self.delay = delay

    def children(self):
        nodelist = []
        if self.delay and isinstance(self.delay, Node):
            nodelist.append(self.delay)
        return tuple(nodelist)


class InstanceList(Node):
    attr_names = ('module',)

    def __init__(self, module_name_str, parameterlist, instances, lineno=0):
        super().__init__(lineno)
        self.module = module_name_str
        self.parameterlist = parameterlist
        self.instances = instances

    def children(self):
        nodelist = []
        if self.parameterlist:
            nodelist.extend(p for p in self.parameterlist if isinstance(p, Node))
        if self.instances:
            nodelist.extend(i for i in self.instances if isinstance(i, Node))
        return tuple(nodelist)


class Instance(Node):
    attr_names = ('name', 'module')

    def __init__(self, module_type_str, name_str, portlist, parameterlist, array=None,
                 lineno=0):
        super().__init__(lineno)
        self.module = module_type_str
        self.name = name_str
        self.portlist = portlist
        self.parameterlist = parameterlist
        self.array = array
        self.design_meta.update({'module_type': module_type_str})

    def children(self):
        nodelist = []
        if self.array and isinstance(self.array, Node):
            nodelist.append(self.array)
        if self.parameterlist:
            nodelist.extend(p for p in self.parameterlist if isinstance(p, Node))
        if self.portlist:
            nodelist.extend(p for p in self.portlist if isinstance(p, Node))
        return tuple(nodelist)


class ParamArg(Node):
    attr_names = ('paramname',)

    def __init__(self, paramname_str, argname_node, lineno=0):
        super().__init__(lineno)
        self.paramname = paramname_str
        self.argname = argname_node

    def children(self):
        nodelist = []
        if self.argname and isinstance(self.argname, Node):
            nodelist.append(self.argname)
        return tuple(nodelist)


class PortArg(Node):
    attr_names = ('portname',)

    def __init__(self, portname_str_or_none, argname_node, lineno=0):
        super().__init__(lineno)
        self.portname = portname_str_or_none
        self.argname = argname_node

    def children(self):
        nodelist = []
        if self.argname and isinstance(self.argname, Node):
            nodelist.append(self.argname)
        return tuple(nodelist)


class Function(Node):
    attr_names = ('name',)

    def __init__(self, name_str, retwidth_node, statement_nodes, lineno=0):
        super().__init__(lineno)
        self.name = name_str
        self.retwidth = retwidth_node
        self.statement = statement_nodes

    def children(self):
        nodelist = []
        if self.retwidth and isinstance(self.retwidth, Node):
            nodelist.append(self.retwidth)
        if self.statement:
            nodelist.extend(s for s in self.statement if isinstance(s, Node))
        return tuple(nodelist)

    def __repr__(self):
        return self.name


class FunctionCall(Node):
    attr_names = ()

    def __init__(self, name_node, args_list, lineno=0):
        super().__init__(lineno)
        self.name = name_node
        self.args = args_list

    def children(self):
        nodelist = []
        if self.name and isinstance(self.name, Node):
            nodelist.append(self.name)
        if self.args:
            nodelist.extend(a for a in self.args if isinstance(a, Node))
        return tuple(nodelist)

    def __repr__(self):
        return self.name.__repr__()


class Task(Node):
    attr_names = ('name',)

    def __init__(self, name_str, statement_nodes, lineno=0):
        super().__init__(lineno)
        self.name = name_str
        self.statement = statement_nodes

    def children(self):
        nodelist = []
        if self.statement:
            nodelist.extend(s for s in self.statement if isinstance(s, Node))
        return tuple(nodelist)


class TaskCall(Node):
    attr_names = ()

    def __init__(self, name_node, args_list, lineno=0):
        super().__init__(lineno)
        self.name = name_node
        self.args = args_list

    def children(self):
        nodelist = []
        if self.name and isinstance(self.name, Node):
            nodelist.append(self.name)
        if self.args:
            nodelist.extend(a for a in self.args if isinstance(a, Node))
        return tuple(nodelist)


class GenerateStatement(Node):
    attr_names = ()

    def __init__(self, items_list, lineno=0):
        super().__init__(lineno)
        self.items = items_list

    def children(self):
        nodelist = []
        if self.items:
            nodelist.extend(i for i in self.items if isinstance(i, Node))
        return tuple(nodelist)


class SystemCall(Node):
    attr_names = ('syscall',)

    def __init__(self, syscall_name_str, args_list, lineno=0):
        super().__init__(lineno)
        self.syscall = syscall_name_str
        self.args = args_list

    def children(self):
        nodelist = []
        if self.args:
            nodelist.extend(a for a in self.args if isinstance(a, Node))
        return tuple(nodelist)

    def __repr__(self):
        ret = ['(', '$', self.syscall]
        for a_node in self.args:
            ret.append(' ')
            ret.append(str(a_node))
        ret.append(')')
        return ''.join(ret)


class IdentifierScopeLabel(Node):
    attr_names = ('name', 'loop')

    def __init__(self, name_str, loop_node=None, lineno=0):
        super().__init__(lineno)
        self.name = name_str
        self.loop = loop_node

    def children(self):
        nodelist = []

        return tuple(nodelist)


class IdentifierScope(Node):
    attr_names = ()

    def __init__(self, labellist_nodes, lineno=0):
        super().__init__(lineno)
        self.labellist = labellist_nodes

    def children(self):
        nodelist = []
        if self.labellist:
            nodelist.extend(l for l in self.labellist if isinstance(l, Node))
        return tuple(nodelist)


class Pragma(Node):
    attr_names = ()

    def __init__(self, entry_node, lineno=0):
        super().__init__(lineno)
        self.entry = entry_node

    def children(self):
        nodelist = []
        if self.entry and isinstance(self.entry, Node):
            nodelist.append(self.entry)
        return tuple(nodelist)


class PragmaEntry(Node):
    attr_names = ('name',)

    def __init__(self, name_str, value_node=None, lineno=0):
        super().__init__(lineno)
        self.name = name_str
        self.value = value_node

    def children(self):
        nodelist = []
        if self.value and isinstance(self.value, Node):
            nodelist.append(self.value)
        return tuple(nodelist)


class Disable(Node):
    attr_names = ('dest',)

    def __init__(self, dest_str, lineno=0):
        super().__init__(lineno)
        self.dest = dest_str

    def children(self):
        return tuple()


class ParallelBlock(Node):
    attr_names = ('scope',)

    def __init__(self, statements_list, scope_str=None, lineno=0):
        super().__init__(lineno)
        self.statements = statements_list
        self.scope = scope_str

    def children(self):
        nodelist = []
        if self.statements:
            nodelist.extend(s for s in self.statements if isinstance(s, Node))
        return tuple(nodelist)


class SingleStatement(Node):
    attr_names = ()

    def __init__(self, statement_node, lineno=0):
        super().__init__(lineno)
        self.statement = statement_node

    def children(self):
        nodelist = []
        if self.statement and isinstance(self.statement, Node):
            nodelist.append(self.statement)
        return tuple(nodelist)


class EmbeddedCode(Node):
    attr_names = ('code',)

    def __init__(self, code_str, lineno=0):
        super().__init__(lineno)
        self.code = code_str

    def children(self):
        return tuple()



class Logic(Wire):
    def __str__(self): return f"Logic({self.name})"



class SimilarityEngine:
    def __init__(self, threshold=0.6, num_perm=128):
        self.lsh = MinHashLSH(threshold=threshold, num_perm=num_perm)
        self.num_perm = num_perm
        self.node_registry = {}
        self.feature_cache = {}

    def add_expression(self, node):
        node_hash = node.compute_hash()
        features = self._extract_features(node)

        mh = MinHash(num_perm=self.num_perm)
        for feature in features:
            mh.update(feature.encode())

        self.lsh.insert(node_hash, mh)
        self.node_registry[node_hash] = node

    def query(self, node, top_k=5):

        features = self._extract_features(node)

        mh = MinHash(num_perm=self.num_perm)
        for feature in features:
            mh.update(feature.encode())

        similar_hashes = self.lsh.query(mh)
        similar_nodes = []
        for h in similar_hashes[:top_k]:
            if h in self.node_registry:

                if self.node_registry[h] is not node:
                    similar_nodes.append(self.node_registry[h])
        return similar_nodes

    def _extract_features(self, node):


        node_unique_id = id(node)

        if node_unique_id in self.feature_cache:
            return self.feature_cache[node_unique_id]

        features = [f"TYPE:{node.__class__.__name__}"]

        stack = [(node, [node.__class__.__name__])]
        visited_for_features = set()

        while stack:
            current, path = stack.pop()

            if id(current) in visited_for_features: continue
            visited_for_features.add(id(current))

            path_str = '>'.join(path)
            features.append(f"PATH_TYPE:{path_str}")


            if hasattr(current, 'attr_names'):
                for attr_name in current.attr_names:
                    attr_val = getattr(current, attr_name, None)
                    if isinstance(attr_val, (str, int, float, bool)):
                        features.append(f"ATTR:{path_str}:{attr_name}={attr_val}")

            children_nodes = list(current.children())
            for i, child in enumerate(reversed(children_nodes)):
                if child and isinstance(child, Node):

                    child_path_element = child.__class__.__name__

                    stack.append((child, path + [child_path_element]))

        self.feature_cache[node_unique_id] = features
        return features


class VerilogASTConverter:


    def __init__(self):
        self.node_cache = {}
        self.debug = False

    def convert(self, node):
        if node is None:
            return None

        node_id = id(node)
        if node_id in self.node_cache:

            if self.node_cache[node_id] is None:
                return Identifier(f"CIRCULAR_REF_TO_{node.__class__.__name__}", lineno=getattr(node, 'lineno', 0))
            return self.node_cache[node_id]

        method_name = 'visit_' + node.__class__.__name__
        visitor = getattr(self, method_name, self.generic_visit)

        self.node_cache[node_id] = None
        converted_node = visitor(node)

        self.node_cache[node_id] = converted_node
        return converted_node

    def generic_visit(self, node):
        children = []
        if hasattr(node, 'children'):
            for child in node.children():
                children.append(self.convert(child))

        if self.debug:
            print(f"Info: Generic visit for node type: {node.__class__.__name__}")

        if len(children) == 1:
            return children[0]
        return Block(children, lineno=getattr(node, 'lineno', 0))


    def visit_Source(self, node: pyverilog_ast.Source):
        description = self.convert(node.description)
        return Source(node.name, description, lineno=node.lineno)

    def visit_Description(self, node: pyverilog_ast.Description):
        definitions = [self.convert(d) for d in node.definitions if d]
        return Description(definitions, lineno=node.lineno)

    def visit_ModuleDef(self, node: pyverilog_ast.ModuleDef):
        paramlist = self.convert(node.paramlist)
        portlist = self.convert(node.portlist)
        items = [self.convert(item) for item in node.items] if node.items else []
        return ModuleDef(node.name, paramlist, portlist, items, default_nettype=node.default_nettype,
                         lineno=node.lineno, end_lineno=getattr(node, 'end_lineno', 0))

    def visit_Paramlist(self, node: pyverilog_ast.Paramlist):
        params = [self.convert(p) for p in node.params] if node.params else []
        return Paramlist(params, lineno=node.lineno)

    def visit_Parameter(self, node: pyverilog_ast.Parameter):
        value = self.convert(node.value)
        width = self.convert(node.width)
        return Parameter(node.name, value, width=width, signed=getattr(node, 'signed', False), lineno=node.lineno)

    def visit_Localparam(self, node: pyverilog_ast.Localparam):
        value = self.convert(node.value)
        width = self.convert(node.width)
        return Localparam(node.name, value, width=width, signed=getattr(node, 'signed', False), lineno=node.lineno)

    def visit_Portlist(self, node: pyverilog_ast.Portlist):
        ports = [self.convert(p) for p in node.ports] if node.ports else []
        return Portlist(ports, lineno=node.lineno)

    def visit_Port(self, node: pyverilog_ast.Port):
        return Identifier(node.name, lineno=node.lineno)

    def visit_Decl(self, node: pyverilog_ast.Decl):
        decls = [self.convert(item) for item in node.list] if node.list else []
        if len(decls) == 1: return decls[0]
        return Decl(decls, lineno=node.lineno)

    def _create_var_from_decl(self, node, CustomVarClass):
        name = node.name
        width = self.convert(node.width)
        signed = getattr(node, 'signed', False)
        value = self.convert(getattr(node, 'value', None))
        dimensions = self.convert(getattr(node, 'dimensions', None))
        return CustomVarClass(name, width, signed, dimensions, value, lineno=node.lineno)

    def visit_Input(self, node: pyverilog_ast.Input):
        return self._create_var_from_decl(node, Input)

    def visit_Output(self, node: pyverilog_ast.Output):
        return self._create_var_from_decl(node, Output)

    def visit_Inout(self, node: pyverilog_ast.Inout):
        return self._create_var_from_decl(node, Inout)

    def visit_Wire(self, node: pyverilog_ast.Wire):
        return self._create_var_from_decl(node, Wire)

    def visit_Reg(self, node: pyverilog_ast.Reg):
        return self._create_var_from_decl(node, Reg)

    def visit_Integer(self, node: pyverilog_ast.Integer):
        return self._create_var_from_decl(node, Integer)

    def visit_Genvar(self, node: pyverilog_ast.Genvar):
        return self._create_var_from_decl(node, Genvar)

    def visit_Tri(self, node: pyverilog_ast.Tri):
        return self._create_var_from_decl(node, Tri)

    def visit_Ioport(self, node: pyverilog_ast.Ioport):
        return self.convert(node.first)


    def visit_Assign(self, node: pyverilog_ast.Assign):
        return Assign(self.convert(node.left), self.convert(node.right), lineno=node.lineno)

    def visit_Always(self, node: pyverilog_ast.Always):
        sens_list = self.convert(node.sens_list)
        statement = self.convert(node.statement)



        if hasattr(node, 'at') and node.at:
            at_str = str(node.at).lower()
            if 'always_comb' in at_str:
                return AlwaysComb(sens_list, statement, lineno=node.lineno)
            if 'always_ff' in at_str:
                return AlwaysFF(sens_list, statement, lineno=node.lineno)
            if 'always_latch' in at_str:
                return AlwaysLatch(sens_list, statement, lineno=node.lineno)


        return Always(sens_list, statement, lineno=node.lineno)

    def visit_Initial(self, node: pyverilog_ast.Initial):
        return Initial(self.convert(node.statement), lineno=node.lineno)

    def visit_Block(self, node: pyverilog_ast.Block):
        statements = [self.convert(s) for s in node.statements] if node.statements else []
        return Block(statements, scope=node.scope, lineno=node.lineno)

    def visit_IfStatement(self, node: pyverilog_ast.IfStatement):
        return IfStatement(self.convert(node.cond), self.convert(node.true_statement),
                           self.convert(node.false_statement), lineno=node.lineno)

    def visit_CaseStatement(self, node: pyverilog_ast.CaseStatement):
        comp = self.convert(node.comp)
        caselist = [self.convert(c) for c in node.caselist] if node.caselist else []
        CaseClass = {'casex': CasexStatement, 'casez': CasezStatement}.get(node.__class__.__name__.lower(),
                                                                           CaseStatement)
        return CaseClass(comp, caselist, lineno=node.lineno)

    def visit_Case(self, node: pyverilog_ast.Case):
        cond = [self.convert(c) for c in node.cond] if node.cond else []
        return Case(cond, self.convert(node.statement), lineno=node.lineno)

    def visit_ForStatement(self, node: pyverilog_ast.ForStatement):
        return ForStatement(self.convert(node.pre), self.convert(node.cond), self.convert(node.post),
                            self.convert(node.statement), lineno=node.lineno)

    def visit_WhileStatement(self, node: pyverilog_ast.WhileStatement):
        return WhileStatement(self.convert(node.cond), self.convert(node.statement), lineno=node.lineno)

    def visit_BlockingSubstitution(self, node: pyverilog_ast.BlockingSubstitution):
        return BlockingSubstitution(self.convert(node.left), self.convert(node.right), lineno=node.lineno)

    def visit_NonblockingSubstitution(self, node: pyverilog_ast.NonblockingSubstitution):
        return NonblockingSubstitution(self.convert(node.left), self.convert(node.right), lineno=node.lineno)


    def visit_InstanceList(self, node: pyverilog_ast.InstanceList):
        instances = []
        paramlist = [self.convert(p) for p in node.parameterlist] if node.parameterlist else []
        for inst in node.instances:
            portlist = [self.convert(p) for p in inst.portlist] if inst.portlist else []
            instance_node = Instance(module_type_str=node.module, name_str=inst.name, portlist=portlist,
                                     parameterlist=paramlist, array=self.convert(inst.array), lineno=inst.lineno)
            instances.append(instance_node)
        if len(instances) == 1: return instances[0]
        return Decl(instances, lineno=node.lineno)


    def visit_Lvalue(self, node: pyverilog_ast.Lvalue):
        return Lvalue(self.convert(node.var))

    def visit_Rvalue(self, node: pyverilog_ast.Rvalue):
        return Rvalue(self.convert(node.var))

    def visit_SensList(self, node: pyverilog_ast.SensList):
        return SensList([self.convert(s) for s in node.list] if node.list else [], lineno=node.lineno)

    def visit_Sens(self, node: pyverilog_ast.Sens):
        return Sens(self.convert(node.sig), type=node.type, lineno=node.lineno)

    def visit_Width(self, node: pyverilog_ast.Width):
        return Width(self.convert(node.msb), self.convert(node.lsb), lineno=node.lineno)

    def visit_Identifier(self, node: pyverilog_ast.Identifier):
        return Identifier(node.name, scope=self.convert(node.scope), lineno=node.lineno)

    def visit_IntConst(self, node: pyverilog_ast.IntConst):
        return IntConst(node.value, lineno=node.lineno)

    def visit_FloatConst(self, node: pyverilog_ast.FloatConst):
        return FloatConst(node.value, lineno=node.lineno)

    def visit_StringConst(self, node: pyverilog_ast.StringConst):
        return StringConst(node.value, lineno=node.lineno)

    def visit_Cond(self, node: pyverilog_ast.Cond):
        return Cond(self.convert(node.cond), self.convert(node.true_value), self.convert(node.false_value),
                    lineno=node.lineno)

    def visit_Concat(self, node: pyverilog_ast.Concat):
        return Concat([self.convert(i) for i in node.list], lineno=node.lineno)

    def visit_LConcat(self, node: pyverilog_ast.LConcat):
        return LConcat([self.convert(i) for i in node.list], lineno=node.lineno)

    def visit_Repeat(self, node: pyverilog_ast.Repeat):
        return Repeat(self.convert(node.value), self.convert(node.times), lineno=node.lineno)

    def visit_Pointer(self, node: pyverilog_ast.Pointer):
        return Pointer(self.convert(node.var), self.convert(node.ptr), lineno=node.lineno)

    def visit_Partselect(self, node: pyverilog_ast.Partselect):
        return Partselect(self.convert(node.var), self.convert(node.msb), self.convert(node.lsb), lineno=node.lineno)

    def visit_ParamArg(self, node: pyverilog_ast.ParamArg):
        return ParamArg(node.paramname, self.convert(node.argname), lineno=node.lineno)

    def visit_PortArg(self, node: pyverilog_ast.PortArg):
        return PortArg(node.portname, self.convert(node.argname), lineno=node.lineno)



    def visit_GenerateStatement(self, node: pyverilog_ast.GenerateStatement):
        items = [self.convert(item) for item in node.items] if node.items else []
        return GenerateStatement(items, lineno=node.lineno)

    def visit_Function(self, node: pyverilog_ast.Function):
        retwidth = self.convert(node.retwidth)
        statements = [self.convert(node.statement)] if node.statement else []
        return Function(node.name, retwidth, statements, lineno=node.lineno)

    def visit_Task(self, node: pyverilog_ast.Task):
        statements = [self.convert(node.statement)] if node.statement else []
        return Task(node.name, statements, lineno=node.lineno)

    def visit_FunctionCall(self, node: pyverilog_ast.FunctionCall):
        name = self.convert(node.name)
        args = [self.convert(arg) for arg in node.args] if node.args else []
        return FunctionCall(name, args, lineno=node.lineno)

    def visit_TaskCall(self, node: pyverilog_ast.TaskCall):
        name = self.convert(node.name)
        args = [self.convert(arg) for arg in node.args] if node.args else []
        return TaskCall(name, args, lineno=node.lineno)


    def visit_Disable(self, node: pyverilog_ast.Disable):
        dest = self.convert(node.dest)
        return Disable(dest.name if hasattr(dest, 'name') else str(dest), lineno=node.lineno)

    def visit_WaitStatement(self, node: pyverilog_ast.WaitStatement):
        cond = self.convert(node.cond)
        statement = self.convert(node.statement)
        return WaitStatement(cond, statement, lineno=node.lineno)

    def visit_EventStatement(self, node: pyverilog_ast.EventStatement):
        event = self.convert(node.senslist)
        return EventStatement(event, lineno=node.lineno)

    def visit_Block(self, node: pyverilog_ast.Block):
        statements = [self.convert(s) for s in node.statements] if node.statements else []

        if node.scope and node.scope.startswith('fork'):
            p_block = ParallelBlock(statements, scope=node.scope, lineno=node.lineno)
            if 'join_any' in node.scope:
                p_block.design_meta['join_type'] = 'join_any'
            elif 'join_none' in node.scope:
                p_block.design_meta['join_type'] = 'join_none'
            else:
                p_block.design_meta['join_type'] = 'join'
            return p_block


        return Block(statements, scope=node.scope, lineno=node.lineno)

    def visit_GenerateStatement(self, node: pyverilog_ast.GenerateStatement):
        items = [self.convert(item) for item in node.items] if node.items else []
        return GenerateStatement(items, lineno=node.lineno)

    def visit_Function(self, node: pyverilog_ast.Function):
        retwidth = self.convert(node.retwidth)
        statements = [self.convert(node.statement)] if node.statement else []
        return Function(node.name, retwidth, statements, lineno=node.lineno)

    def visit_Task(self, node: pyverilog_ast.Task):
        statements = [self.convert(node.statement)] if node.statement else []
        return Task(node.name, statements, lineno=node.lineno)

    def visit_FunctionCall(self, node: pyverilog_ast.FunctionCall):
        name = self.convert(node.name)
        args = [self.convert(arg) for arg in node.args] if node.args else []
        return FunctionCall(name, args, lineno=node.lineno)

    def visit_TaskCall(self, node: pyverilog_ast.TaskCall):
        name = self.convert(node.name)
        args = [self.convert(arg) for arg in node.args] if node.args else []
        return TaskCall(name, args, lineno=node.lineno)

    def visit_Disable(self, node: pyverilog_ast.Disable):
        dest = self.convert(node.dest)
        dest_name = dest.name if hasattr(dest, 'name') else str(dest)
        return Disable(dest_name, lineno=node.lineno)

    def visit_WaitStatement(self, node: pyverilog_ast.WaitStatement):
        cond = self.convert(node.cond)
        statement = self.convert(node.statement)
        return WaitStatement(cond, statement, lineno=node.lineno)

    def visit_EventStatement(self, node: pyverilog_ast.EventStatement):
        event = self.convert(node.senslist)
        return EventStatement(event, lineno=node.lineno)

    def visit_SystemCall(self, node: pyverilog_ast.SystemCall):
        args = [self.convert(arg) for arg in node.args] if node.args else []
        return SystemCall(node.syscall, args, lineno=node.lineno)

    def __getattr__(self, name):
        if name.startswith('visit_'):
            op_name = name[6:]
            op_map = {'Uplus': Uplus, 'Uminus': Uminus, 'Ulnot': Ulnot, 'Unot': Unot, 'Uand': Uand, 'Unand': Unand,
                      'Uor': Uor, 'Unor': Unor, 'Uxor': Uxor, 'Uxnor': Uxnor, 'Power': Power, 'Times': Times,
                      'Divide': Divide, 'Mod': Mod, 'Plus': Plus, 'Minus': Minus, 'Sll': Sll, 'Srl': Srl, 'Sla': Sla,
                      'Sra': Sra, 'LessThan': LessThan, 'GreaterThan': GreaterThan, 'LessEq': LessEq,
                      'GreaterEq': GreaterEq,
                      'Eq': Eq, 'NotEq': NotEq, 'Eql': Eql, 'NotEql': NotEql, 'And': And, 'Xor': Xor, 'Xnor': Xnor,
                      'Or': Or, 'Land': Land, 'Lor': Lor}
            if op_name in op_map:
                OpClass = op_map[op_name]
                if issubclass(OpClass, UnaryOperator):
                    return lambda node: OpClass(self.convert(node.right), lineno=node.lineno)
                else:
                    return lambda node: OpClass(self.convert(node.left), self.convert(node.right), lineno=node.lineno)
        raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")

class RTLParser:

    def __init__(self, debug: bool = False):
        self.debug: bool = debug
        self.pyverilog_ast_root: Optional[pyverilog_ast.Source] = None
        self.directives: List[Any] = []
        self.metadata: dict = {
            'source_type': 'RTL_DESIGN',
            'modules': [],
            'interfaces': [],
            'packages': [],
            'line_map': {},
            'files_parsed': [],
            'clock_domains': {},
            'parameters': {},
            'sensitivity': {}
        }
        self._is_systemverilog: bool = False

        self.original_source_code = None
        self.original_file_path = None
        self.original_file_name = None

        self._comment_pattern_multiline = re.compile(r'/\*.*?\*/', re.DOTALL | re.ASCII)
        self._comment_pattern_singleline = re.compile(r'//.*', re.ASCII)

        self._module_pattern = re.compile(r'\bmodule\s+([a-zA-Z0-9_]+)', re.IGNORECASE | re.ASCII)

        self._parameter_pattern = re.compile(r'\bparameter\s+([a-zA-Z0-9_]+)\s*=\s*([^,;]+)', re.ASCII)


        self._sensitivity_pattern = re.compile(r'(posedge|negedge)\s+([a-zA-Z0-9_]+)', re.ASCII)

        self._module_def_pattern = re.compile(
            r'\bmodule\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*'  
            r'(#\s*\((.*?)\))?\s*'  
            r'(\(([^)]*)\))?\s*;',
            re.DOTALL | re.IGNORECASE | re.ASCII
        )

        self._wire_decl_pattern = re.compile(
            r'\bwire\s+(?:\[\s*([^:]+)\s*:\s*([^\]]+)\s*\]\s*)?([a-zA-Z0-9_,\s]+)\s*;',
            re.ASCII
        )
        self._reg_decl_pattern = re.compile(
            r'\breg\s+(?:\[\s*([^:]+)\s*:\s*([^\]]+)\s*\]\s*)?([a-zA-Z0-9_,\s]+)\s*;',
            re.ASCII
        )
        self._logic_decl_pattern = re.compile(
            r'\blogic\s+(?:\[\s*([^:]+)\s*:\s*([^\]]+)\s*\]\s*)?([a-zA-Z0-9_,\s]+)\s*;',

            re.ASCII
        )
        self._assign_stmt_pattern = re.compile(r'\bassign\s+(.*?)\s*=\s*([^;]+);', re.ASCII)

        self._always_block_pattern = re.compile(

            r'\b(always|always_ff|always_comb|always_latch)\s*@\s*\((.*?)\)\s*(.*?)(?=(?:\b(?:always|initial|module|endmodule|task|function)\b)|$)',
            re.DOTALL | re.IGNORECASE | re.ASCII
        )

        self._instance_pattern = re.compile(
            r'\b([a-zA-Z_][a-zA-Z0-9_]*)\s+' 
            r'(?:#\s*\((.*?)\)\s*)?'  
            r'\b([a-zA-Z_][a-zA-Z0-9_]*)\s*'  
            r'\((.*?)\)\s*;',
            re.DOTALL | re.IGNORECASE | re.ASCII
        )

        self._initial_block_pattern = re.compile(
            r'\binitial\s*(.*?)(?=(?:\b(?:always|module|endmodule|task|function|initial)\b)|$)',
            re.DOTALL | re.IGNORECASE | re.ASCII
        )

        self._endmodule_pattern = re.compile(r'\bendmodule\b', re.ASCII)


        self.similarity_engine = SimilarityEngine()



    def parse_folder(self, folder_path: str, recursive: bool = True,
                     extensions: tuple = ('.v', '.sv', '.vh')) -> 'RTLParser':
        if not os.path.isdir(folder_path):
            raise ValueError(f"Invalid folder path: {folder_path}")

        self.metadata = {
            'source_type': 'RTL_DESIGN_FOLDER',
            'modules': [], 'interfaces': [], 'packages': [],
            'line_map': {}, 'files_parsed': [], 'clock_domains': {},
            'parameters': {}, 'sensitivity': {}
        }
        all_module_defs = []

        verilog_files = []
        if recursive:
            for root, _, files in os.walk(folder_path):
                for file in files:
                    if file.lower().endswith(extensions):
                        verilog_files.append(os.path.join(root, file))
        else:
            for file_name in os.listdir(folder_path):
                if file_name.lower().endswith(extensions):
                    verilog_files.append(os.path.join(folder_path, file_name))

        if not verilog_files:
            print(f"No files with extensions {extensions} found in {folder_path}")
            self.ast = Source(folder_path, Description([]))
            return self

        aggregated_line_map = {}
        file_base_line_offset = 0

        for file_path in verilog_files:
            try:
                if self.debug: print(f"Parsing file in folder: {file_path}")
                current_file_parser = RTLParser(debug=self.debug)
                current_file_parser.parse_file(file_path)

                if current_file_parser.ast and current_file_parser.ast.description:
                    for definition_node in current_file_parser.ast.description.definitions:
                        if isinstance(definition_node, ModuleDef):
                            all_module_defs.append(definition_node)

                self.metadata['files_parsed'].append(file_path)
                self.metadata['modules'].extend(current_file_parser.metadata['modules'])
                self.metadata['line_map'][file_path] = current_file_parser.metadata['line_map']

                for k, v_item in current_file_parser.metadata['parameters'].items():
                    if k not in self.metadata['parameters']: self.metadata['parameters'][k] = v_item
                for k, v_item in current_file_parser.metadata['sensitivity'].items():
                    if k not in self.metadata['sensitivity']: self.metadata['sensitivity'][k] = v_item

            except Exception as e:
                print(f"Error parsing file {file_path}: {str(e)}")
                continue

        self.metadata['modules'] = sorted(list(set(self.metadata['modules'])))
        folder_description = Description(all_module_defs, lineno=0)
        self.ast = Source(name=folder_path, description=folder_description, lineno=0)

        return self

    def _validate_rtl(self, code: str) -> bool:
        if not self._module_pattern.search(code):
            print("Warning: 'module' keyword not found. This might not be a standard Verilog design file.")
        return True

    def remove_comments_and_map_lines(self, code: str) -> Tuple[str, Dict[int, int]]:
        lines = code.split('\n')
        clean_lines = []
        line_map = {}
        in_multiline_comment = False
        synthesis_directive_pattern = re.compile(r"//\s*(synthesis|synopsys)", re.IGNORECASE | re.ASCII)

        for original_line_idx, line_content in enumerate(lines):
            original_line_num = original_line_idx + 1
            processed_line = ""
            i = 0
            current_line_text = line_content
            temp_line_after_multiline_removal = ""
            scan_idx = 0
            while scan_idx < len(current_line_text):
                if in_multiline_comment:
                    end_comment_idx = current_line_text.find('*/', scan_idx)
                    if end_comment_idx != -1:
                        in_multiline_comment = False
                        scan_idx = end_comment_idx + 2
                    else:
                        scan_idx = len(current_line_text)
                else:
                    start_comment_idx = current_line_text.find('/*', scan_idx)
                    single_line_comment_idx = current_line_text.find('//', scan_idx)

                    if start_comment_idx != -1 and (
                            single_line_comment_idx == -1 or start_comment_idx < single_line_comment_idx):
                        temp_line_after_multiline_removal += current_line_text[scan_idx:start_comment_idx]
                        in_multiline_comment = True
                        scan_idx = start_comment_idx + 2
                        end_comment_idx_inline = current_line_text.find('*/', scan_idx)
                        if end_comment_idx_inline != -1:
                            in_multiline_comment = False
                            scan_idx = end_comment_idx_inline + 2
                        else:
                            scan_idx = len(current_line_text)
                    elif single_line_comment_idx != -1:
                        if synthesis_directive_pattern.search(current_line_text[single_line_comment_idx:]):
                            temp_line_after_multiline_removal += current_line_text[scan_idx:]
                        else:
                            temp_line_after_multiline_removal += current_line_text[scan_idx:single_line_comment_idx]
                        scan_idx = len(current_line_text)
                    else:
                        temp_line_after_multiline_removal += current_line_text[scan_idx:]
                        scan_idx = len(current_line_text)

            processed_line = temp_line_after_multiline_removal

            if processed_line.strip():
                clean_lines.append(processed_line)
                line_map[original_line_num] = len(clean_lines)

        return '\n'.join(clean_lines), line_map

    def parse_file(self, filepath: str) -> 'RTLParser':
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"File not found: {filepath}")
        if self.debug: print(f"Starting to parse file: {filepath}")

        self._is_systemverilog = filepath.lower().endswith(('.sv', '.svh'))

        encodings_to_try = ['utf-8','gbk', 'latin-1', 'ascii']
        code = None
        for enc in encodings_to_try:
            try:
                with open(filepath, 'r', encoding=enc) as f:
                    code = f.read()
                if self.debug: print(f"Successfully read file {filepath} with encoding {enc}")
                break
            except UnicodeDecodeError:
                if self.debug: print(f"Failed to decode {filepath} with {enc}")
                continue
            except Exception as e:
                raise IOError(f"Error reading file {filepath}: {e}")

        if code is None:
            raise RTLFormatError(f"Could not decode file {filepath} with tried encodings.")

        self.original_source_code = code
        self.original_file_path = filepath
        self.original_file_name = os.path.basename(filepath)

        self.metadata['files_parsed'].append(filepath)


        self.parse_code(code, filepath)
        return self

    def parse_code(self, code: str, source_name: str = "<string>") -> 'RTLParser':
        if self.debug: print(f"Parsing code from source: {source_name}")

        clean_code, line_map = self.remove_comments_and_map_lines(code)
        self.metadata['line_map'] = line_map

        self.ast = self._build_ast(clean_code, source_name)

        if self.ast and self.ast.description:
            parsed_module_names = [m.name for m in self.ast.description.definitions if isinstance(m, ModuleDef)]
            self.metadata['modules'] = list(set(self.metadata.get('modules', []) + parsed_module_names))
        if self.debug and self.ast:
            print(f"AST built for {source_name}. Root: {self.ast.__class__.__name__}")

        return self

    def parse_json(self, json_input: Union[str, Dict]) -> 'RTLParser':
        if self.debug: print("Parsing from JSON input")
        if isinstance(json_input, str):
            try:
                json_input_dict = json.loads(json_input)
            except json.JSONDecodeError as e:
                raise RTLFormatError(f"Invalid JSON input: {e}")
        elif isinstance(json_input, dict):
            json_input_dict = json_input
        else:
            raise RTLFormatError("JSON input must be a string or a dictionary")

        if 'raw_code' not in json_input_dict:
            raise RTLFormatError("JSON input missing 'raw_code' field")

        source_name = json_input_dict.get('file_name', "<json_input>")
        return self.parse_code(json_input_dict['raw_code'], source_name)

    def _get_line_number(self, pos: int, code_context: str) -> int:

        return code_context[:pos].count('\n') + 1

    def _build_ast(self, clean_code: str, source_name: str) -> Source:

        with tempfile.NamedTemporaryFile(mode='w+', suffix='.v', delete=False) as temp_f:
            temp_f.write(clean_code)
            temp_filepath = temp_f.name

        try:
            ast_tuple = pyverilog_parse([temp_filepath], debug=False)
            pyverilog_ast = ast_tuple[0]
            self.directives = ast_tuple[1]

            if self.debug:
                print(f"Successfully parsed with pyverilog. Root: {pyverilog_ast.__class__.__name__}")

        except ParseError as e:
            print(f"SYNTAX ERROR in '{source_name}' (reported by pyverilog): {str(e)}")
            return Source(source_name, Description([]), lineno=0)
        except Exception as e:
            print(f"UNEXPECTED PARSING-PHASE ERROR in '{source_name}': {str(e)}")
            traceback.print_exc()
            return Source(source_name, Description([]), lineno=0)
        finally:
            if os.path.exists(temp_filepath):
                os.remove(temp_filepath)

        if pyverilog_ast is None:
            if self.debug:
                print(f"Warning: pyverilog parsing resulted in a None AST for '{source_name}'.")
            return Source(source_name, Description([]), lineno=0)

        try:
            converter = VerilogASTConverter()
            converter.debug = self.debug
            custom_ast = converter.convert(pyverilog_ast)

            return custom_ast

        except Exception as e:
            print(f"AST CONVERSION FAILED for '{source_name}': {str(e)}")
            traceback.print_exc()
            return Source(source_name, Description([]), lineno=0)

    def _find_endmodule(self, code: str, start_pos: int, module_name: str):
        match = self._endmodule_pattern.search(code, start_pos)
        if not match and self.debug:
            print(f"Warning: Module '{module_name}' lacks 'endmodule'")
        return match

    def _split_arguments(self, args_str: str) -> List[str]:
        args = []
        current_arg = ""
        paren_count = 0
        bracket_count = 0
        brace_count = 0

        for char in args_str:
            if char == ',' and paren_count == 0 and bracket_count == 0 and brace_count == 0:
                if current_arg.strip():
                    args.append(current_arg.strip())
                current_arg = ""
            else:
                if char == '(':
                    paren_count += 1
                elif char == ')':
                    paren_count -= 1
                elif char == '[':
                    bracket_count += 1
                elif char == ']':
                    bracket_count -= 1
                elif char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                current_arg += char

        if current_arg.strip():
            args.append(current_arg.strip())

        return args


    def build_graph(self) -> DiGraph:
        G = DiGraph()
        if not self.ast:
            if self.debug: print("AST is not built. Cannot build graph.")
            return G
        if self.debug: print("Building DiGraph from AST...")
        self.ast.toplogic_tree_traverse(G)
        if self.debug: print(f"Graph built with {G.number_of_nodes()} nodes and {G.number_of_edges()} edges.")
        return G

    def analyze_clock_domains(self):
        if not self.ast:
            if self.debug: print("AST not available for clock domain analysis.")
            return

        clock_domains = {}
        current_module_name = "unknown_module"

        def visit_node_for_clocks(node,
                                  current_module_clocks_map):
            nonlocal current_module_name

            if isinstance(node, ModuleDef):
                current_module_name = node.name

            if isinstance(node, (AlwaysFF, Always)):
                if node.sens_list and hasattr(node.sens_list, 'list'):
                    for sens_item in node.sens_list.list:
                        if isinstance(sens_item, Sens) and (sens_item.type == 'posedge' or sens_item.type == 'negedge'):
                            if sens_item.sig and isinstance(sens_item.sig, Identifier):
                                clock_name = sens_item.sig.name
                                if clock_name not in current_module_clocks_map:
                                    current_module_clocks_map[clock_name] = {
                                        'type': sens_item.type,
                                        'module': current_module_name,
                                        'dependent_regs': set()
                                    }


            for child in node.children():
                if child: visit_node_for_clocks(child, current_module_clocks_map)

        if self.ast.description and self.ast.description.definitions:
            for definition_node in self.ast.description.definitions:
                if isinstance(definition_node, ModuleDef):
                    visit_node_for_clocks(definition_node, clock_domains)

        self.metadata['clock_domains'] = clock_domains
        if self.debug: print(f"Clock domain analysis (simplified): {clock_domains}")

    def get_ast_as_dict(self) -> Optional[Dict[str, Any]]:
        if not self.ast:
            if self.debug: print("AST not available for dictionary conversion.")
            return None
        ast_dict = self.ast.to_dict()

        if hasattr(self, 'original_source_code') and self.original_source_code:
            ast_dict['source_code'] = self.original_source_code
            ast_dict['source_file'] = getattr(self, 'original_file_name', None)
            ast_dict['source_path'] = getattr(self, 'original_file_path', None)

        return ast_dict

    def get_ast_as_json(self, indent: Optional[int] = 2) -> str:
        ast_dict = self.get_ast_as_dict()
        if ast_dict is None:
            return "{}"
        try:
            return json.dumps(ast_dict, indent=indent)
        except TypeError as e:
            if self.debug: print(f"Error serializing AST to JSON: {e}. Fallback to less readable JSON.")
            return json.dumps(ast_dict, indent=indent, default=str)

    def _to_xml_element(self, data: Union[Dict[str, Any], List[Any], Any], parent_element: ET.Element,
                        key_name: str = "item") -> None:
        if isinstance(data, dict):
            node_type_for_tag = data.get("type", key_name if key_name != "key_" else "object")
            safe_tag = re.sub(r'[^a-zA-Z0-9_.-]', '_', str(node_type_for_tag))
            if not safe_tag or safe_tag[0].isdigit() or not (safe_tag[0].isalpha() or safe_tag[0] == '_'):
                safe_tag = "elem_" + safe_tag
            current_element = ET.SubElement(parent_element, safe_tag)

            for key, value in data.items():
                if key == "type" and current_element.tag == safe_tag: continue

                if isinstance(value, (str, int, float, bool)) and \
                        re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_.-]*', key) and \
                        key not in ["children",
                                    "attributes"]:
                    try:
                        current_element.set(key, str(value))
                    except ValueError:
                        attr_elem = ET.SubElement(current_element, "prop_" + key)
                        attr_elem.text = str(value)
                else:
                    sub_element_tag = key
                    if not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_.-]*', sub_element_tag):
                        sub_element_tag = "prop_" + re.sub(r'[^a-zA-Z0-9_.-]', '_', str(key))
                        if not sub_element_tag or sub_element_tag[
                            0].isdigit(): sub_element_tag = "prop_" + sub_element_tag

                    self._to_xml_element(value, current_element,
                                         key)

        elif isinstance(data, list):

            list_item_tag = key_name if key_name not in ["children",
                                                         "attributes"] else "item"
            if not re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_.-]*', list_item_tag):
                list_item_tag = "item_" + re.sub(r'[^a-zA-Z0-9_.-]', '_', str(list_item_tag))

            for item in data:

                item_element = ET.SubElement(parent_element, list_item_tag)
                self._to_xml_element(item, item_element, "item_content")
        else:
            parent_element.text = str(data) if data is not None else ""

    def get_ast_as_xml(self) -> str:
        ast_dict = self.get_ast_as_dict()
        if ast_dict is None:
            return "<ast/>"

        root_element_name = ast_dict.get("type", "AST_Root")
        root_element_name = re.sub(r'[^a-zA-Z0-9_.-]', '_', root_element_name)
        if not root_element_name or root_element_name[0].isdigit() or not (
                root_element_name[0].isalpha() or root_element_name[0] == '_'):
            root_element_name = "Root_" + root_element_name

        root = ET.Element(root_element_name)
        for key, value in ast_dict.items():
            if key == "type": continue
            if isinstance(value, (str, int, float, bool)) and \
                    re.fullmatch(r'[a-zA-Z_][a-zA-Z0-9_.-]*', key) and \
                    key not in ["children", "attributes"]:
                try:
                    root.set(key, str(value))
                except ValueError:
                    pass

        if 'attributes' in ast_dict and isinstance(ast_dict['attributes'], dict):
            attr_container_tag = "attributes_collection"
            self._to_xml_element(ast_dict['attributes'], root, attr_container_tag)

        if 'children' in ast_dict and isinstance(ast_dict['children'], list):
            children_container_tag = "children_collection"
            self._to_xml_element(ast_dict['children'], root, children_container_tag)

        try:
            from xml.dom import minidom
            xml_str = ET.tostring(root, encoding='unicode')
            xml_str = re.sub(r'\s+[^\s"]+="None"', '', xml_str)

            dom = minidom.parseString(xml_str)
            return dom.toprettyxml(indent="  ")
        except ImportError:
            if self.debug: print("minidom not found for pretty XML, returning compact XML.")
            return ET.tostring(root, encoding='unicode')
        except Exception as e:
            if self.debug: print(f"Error pretty printing XML: {e}. Returning compact XML.")
            return ET.tostring(root, encoding='unicode')

    def visualize_ast_graph(self, output_filepath: str = "ast_graph.png", layout: str = "kamada_kawai") -> None:
        G = self.build_graph()
        if not G or G.number_of_nodes() == 0:
            print("Graph is empty. Cannot visualize.")
            return

        plt.figure(figsize=(20, 15))

        pos_algo_map = {
            "kamada_kawai": nx.kamada_kawai_layout,
            "spring": lambda g: nx.spring_layout(g, k=0.25, iterations=30, seed=42),
            "spectral": nx.spectral_layout,
            "shell": nx.shell_layout,
            "circular": nx.circular_layout,
            "planar": nx.planar_layout,
            "sfdp": None,
        }

        pos = None
        if layout in pos_algo_map:
            if layout == "sfdp":
                try:
                    pos = nx.nx_agraph.graphviz_layout(G, prog="sfdp")
                except ImportError:
                    print("PyGraphviz not found for SFDP layout, trying Kamada-Kawai.")
                    pos = nx.kamada_kawai_layout(G)
                except Exception as e:
                    print(f"SFDP layout failed ({e}), trying Kamada-Kawai.")
                    pos = nx.kamada_kawai_layout(G)

            elif layout == "planar":
                if nx.check_planarity(G)[0]:
                    pos = nx.planar_layout(G)
                else:
                    print(f"Graph is not planar, cannot use planar_layout. Defaulting to kamada_kawai.")
                    pos = nx.kamada_kawai_layout(G)
            else:
                pos = pos_algo_map[layout](G)

        else:
            print(f"Unknown layout '{layout}', defaulting to kamada_kawai_layout.")
            pos = nx.kamada_kawai_layout(G)

        node_labels = {}
        node_colors = []
        node_type_colors = {
            'ModuleDef': 'skyblue', 'Instance': 'lightgreen',
            'Assign': 'salmon', 'Always': 'khaki', 'AlwaysFF': 'gold',
            'Input': 'lightcoral', 'Output': 'lightcoral', 'Wire': 'lightgrey', 'Reg': 'silver',
            'Identifier': 'whitesmoke', 'IntConst': 'azure',
            'Operator': 'lavender', 'Cond': 'thistle',

        }
        default_node_color = 'lightgray'

        for node, data in G.nodes(data=True):
            node_name_str = str(node)
            type_label = data.get('type', node_name_str.split('_L')[0])
            line_info = data.get('line', ('?', '?'))
            line_label = f"L:{line_info[0]}"
            if line_info[0] != line_info[1] and line_info[1] != 0 and line_info[
                1] != '?':
                line_label += f"-{line_info[1]}"


            display_name = node_name_str
            if len(node_name_str) > 30:
                display_name = node_name_str[:15] + "..." + node_name_str[-12:]
            node_labels[node] = f"{display_name}\n({type_label} {line_label})"

            node_colors.append(node_type_colors.get(type_label, default_node_color))

        nx.draw_networkx_nodes(G, pos, node_size=600, node_color=node_colors, alpha=0.9,
                               edgecolors='dimgray')
        nx.draw_networkx_edges(G, pos, arrowstyle='-|>', arrowsize=12, edge_color='dimgray', alpha=0.7,
                               connectionstyle='arc3,rad=0.05')


        nx.draw_networkx_labels(G, pos, labels=node_labels, font_size=7, font_weight='bold')

        plt.title(f"AST Dataflow Graph ({layout} layout)", fontsize=16)
        plt.axis('off')
        plt.tight_layout()
        try:
            plt.savefig(output_filepath, format=output_filepath.split('.')[-1], dpi=300, bbox_inches='tight')
            print(f"AST graph saved to {output_filepath}")
        except Exception as e:
            print(f"Error saving graph: {e}")
        plt.close()

    def to_bio_json(self):
        if not self.ast:
            return []

        bio_data_stream = []

        def process_node_to_bio_stream(node):
            if node is None: return

            node_type_name = node.__class__.__name__
            token_text = ""
            bio_label = f"B-{node_type_name.upper()}"

            if hasattr(node, 'name') and isinstance(node.name, str):
                token_text = node.name
            elif hasattr(node, 'value') and isinstance(node.value, (str, int, float)):
                token_text = str(node.value)
            elif hasattr(node, 'syscall') and isinstance(node.syscall, str):
                token_text = f"${node.syscall}"
            else:
                token_text = node_type_name.lower()

            meta_info = {"lineno": getattr(node, 'lineno', 0)}
            if hasattr(node, 'end_lineno') and node.end_lineno != 0:
                meta_info["end_lineno"] = node.end_lineno
            if hasattr(node, 'attr_names'):
                for attr in node.attr_names:
                    if hasattr(node, attr) and attr not in ['name', 'value']:
                        attr_val = getattr(node, attr)
                        if isinstance(attr_val, (str, int, float, bool)):
                            meta_info[attr] = attr_val

            bio_data_stream.append({"token": token_text, "label": bio_label, "meta": meta_info})


            for child_node in node.children():
                process_node_to_bio_stream(child_node)

        process_node_to_bio_stream(self.ast)
        return bio_data_stream


    def to_semantic_json(self, target_module_name: Optional[str] = None) -> Dict[str, Any]:

        if not self.ast or not self.ast.description:
            return {"ast_structure": {}}

        modules_to_process = []


        for definition in self.ast.description.definitions:
            if isinstance(definition, ModuleDef):
                if target_module_name is None or definition.name == target_module_name:
                    modules_to_process.append(definition)

        if not modules_to_process:
            return {"ast_structure": {}}


        module = modules_to_process[0]

        semantic_json = {
            "ast_structure": {
                "module": self._transform_module_to_semantic(module)
            }
        }

        return semantic_json

    def _transform_module_to_semantic(self, module: ModuleDef) -> Dict[str, Any]:

        module_dict = {
            "name": module.name,
            "type": "module_declaration",
            "node_id": f"mod_{module.lineno:03d}",
            "ports": [],
            "internal_signals": [],
            "continuous_assignments": [],
            "always_blocks": [],
            "instances": []
        }


        port_names = [p.name for p in module.portlist.ports] if module.portlist and module.portlist.ports else []


        decl_items = [item for item in module.items if
                      isinstance(item, (Decl, Input, Output, Inout, Wire, Reg, Integer, Logic))]
        for decl_item in decl_items:
            items_to_process = decl_item.list if isinstance(decl_item, Decl) else [decl_item]
            for var_node in items_to_process:
                if not hasattr(var_node, 'name'): continue


                if var_node.name in port_names:
                    port_info = self._transform_port_to_semantic(var_node)
                    if port_info: module_dict["ports"].append(port_info)
                else:
                    signal_info = self._transform_signal_to_semantic(var_node)
                    if signal_info: module_dict["internal_signals"].append(signal_info)


        for item in module.items:
            if isinstance(item, Assign):
                assign_info = self._transform_assign_to_semantic(item)
                if assign_info: module_dict["continuous_assignments"].append(assign_info)
            elif isinstance(item, (Always, AlwaysFF, AlwaysComb, AlwaysLatch)):
                always_info = self._transform_always_to_semantic(item)
                if always_info: module_dict["always_blocks"].append(always_info)
            elif isinstance(item, Instance):
                module_dict["instances"].append({
                    "instance_name": item.name,
                    "module_type": item.module,
                    "lineno": item.lineno
                })

        return module_dict

    def _transform_port_to_semantic(self, port_node) -> Optional[Dict[str, Any]]:

        if not hasattr(port_node, 'name'): return None

        port_info = {
            "name": port_node.name,
            "direction": "unknown",
            "type": port_node.__class__.__name__.lower(),
            "width": 1,
            "semantic_role": self._infer_semantic_role(port_node.name)
        }

        if isinstance(port_node, Input):
            port_info["direction"] = "input"
        elif isinstance(port_node, Output):
            port_info["direction"] = "output"
        elif isinstance(port_node, Inout):
            port_info["direction"] = "inout"

        if hasattr(port_node, 'width') and port_node.width:
            port_info["width"] = self._calculate_width(port_node.width)

        if "rst" in port_info["name"].lower() or "reset" in port_info["name"].lower():
            port_info["polarity"] = "active_low" if "_n" in port_info["name"] else "active_high"

        return port_info

    def _transform_signal_to_semantic(self, signal_node) -> Optional[Dict[str, Any]]:

        if not hasattr(signal_node, 'name'): return None

        signal_info = {
            "name": signal_node.name,
            "type": signal_node.__class__.__name__.lower(),
            "width": 1
        }

        if hasattr(signal_node, 'width') and signal_node.width:
            width = self._calculate_width(signal_node.width)
            signal_info["width"] = width
            if hasattr(signal_node.width, 'msb') and hasattr(signal_node.width, 'lsb'):
                msb_val = self._evaluate_constant_expr(signal_node.width.msb)
                lsb_val = self._evaluate_constant_expr(signal_node.width.lsb)
                if msb_val is not None and lsb_val is not None:
                    signal_info["range"] = f"[{msb_val}:{lsb_val}]"

        signal_info["semantic_role"] = self._infer_semantic_role(signal_info["name"])
        if "cnt" in signal_info["name"].lower() or "count" in signal_info["name"].lower():
            signal_info["purpose"] = "timing_counter"

        return signal_info

    def _transform_assign_to_semantic(self, assign_node: Assign) -> Optional[Dict[str, Any]]:

        return {
            "type": "continuous_assignment",
            "lineno": assign_node.lineno,
            "target": self._extract_identifier_name(assign_node.left),
            "value": self._transform_expression_to_semantic(assign_node.right)
        }

    def _transform_always_to_semantic(self, always_block: Always) -> Optional[Dict[str, Any]]:
        always_info = {
            "block_id": f"always_{always_block.lineno:03d}",
            "block_type": always_block.__class__.__name__,
            "trigger": self._transform_sensitivity_to_semantic(always_block.sens_list),
            "statement_tree": self._transform_statement_to_semantic(always_block.statement)
        }
        always_info["functional_summary"] = self._infer_functional_summary(always_info["statement_tree"])
        return always_info

    def _transform_sensitivity_to_semantic(self, sens_list) -> Dict[str, Any]:
        if not sens_list or not hasattr(sens_list, 'list') or not sens_list.list:
            return {"type": "unknown"}

        triggers = []
        for sens in sens_list.list:
            if isinstance(sens, Sens):
                signal_name = "any"
                if hasattr(sens.sig, 'name'):
                    signal_name = sens.sig.name

                if sens.type in ['posedge', 'negedge']:
                    triggers.append(f"{sens.type} {signal_name}")
                elif sens.type == 'all':
                    return {"type": "combinational", "trigger": "*"}
                else:
                    triggers.append(signal_name)

        return {"type": "edge_sensitive", "trigger": " or ".join(triggers)}

    def _transform_statement_to_semantic(self, stmt_node: Node) -> Optional[Dict[str, Any]]:
        if not stmt_node: return None

        if isinstance(stmt_node, Block):
            sub_statements = [self._transform_statement_to_semantic(s) for s in stmt_node.statements if s]
            sub_statements = [s for s in sub_statements if s]
            if not sub_statements: return None
            if len(sub_statements) == 1: return sub_statements[0]
            return {"type": "block", "statements": sub_statements}

        elif isinstance(stmt_node, IfStatement):
            if_dict = {
                "type": "conditional",
                "condition": self._transform_expression_to_semantic(stmt_node.cond),
                "true_branch": self._transform_statement_to_semantic(stmt_node.true_statement)
            }
            if stmt_node.false_statement:
                false_branch = self._transform_statement_to_semantic(stmt_node.false_statement)
                if false_branch: if_dict["false_branch"] = false_branch
            return if_dict

        elif isinstance(stmt_node, (NonblockingSubstitution, BlockingSubstitution)):
            return {
                "type": "assignment",
                "assignment_type": "non_blocking" if isinstance(stmt_node, NonblockingSubstitution) else "blocking",
                "target": self._extract_identifier_name(stmt_node.left),
                "value": self._transform_expression_to_semantic(
                    stmt_node.right.var if hasattr(stmt_node.right, 'var') else stmt_node.right)
            }

        elif isinstance(stmt_node, (CaseStatement, CasexStatement, CasezStatement)):
            case_items = []
            if hasattr(stmt_node, 'caselist'):
                for item in stmt_node.caselist:
                    conditions = []
                    if isinstance(item.cond, list):
                        conditions = [self._transform_expression_to_semantic(c) for c in item.cond]
                    elif item.cond:
                        conditions = [self._transform_expression_to_semantic(item.cond)]
                    case_items.append({
                        "conditions": conditions,
                        "body": self._transform_statement_to_semantic(item.statement)
                    })
            return {"type": "case_statement", "match_on": self._transform_expression_to_semantic(stmt_node.comp),
                    "cases": case_items}

        elif isinstance(stmt_node, ForStatement):
            return {"type": "for_loop", "init": self._transform_statement_to_semantic(stmt_node.pre),
                    "condition": self._transform_expression_to_semantic(stmt_node.cond),
                    "update": self._transform_statement_to_semantic(stmt_node.post),
                    "body": self._transform_statement_to_semantic(stmt_node.statement)}

        elif isinstance(stmt_node, SystemCall):
            return {"type": "system_call", "task_name": f"${stmt_node.syscall}",
                    "arguments": [self._transform_expression_to_semantic(arg) for arg in stmt_node.args]}

        return {"type": "unknown_statement", "node_type": stmt_node.__class__.__name__}

    def _transform_expression_to_semantic(self, expr_node: Node) -> Optional[Dict[str, Any]]:
        if not expr_node: return None

        if isinstance(expr_node, IntConst):
            return {"type": "literal", "value": expr_node.value}
        elif isinstance(expr_node, StringConst):
            return {"type": "string_literal", "value": expr_node.value}
        elif isinstance(expr_node, Identifier):
            return {"type": "identifier", "name": expr_node.name}

        elif isinstance(expr_node, (Operator, Cond)):
            op_map = {'Plus': '+', 'Minus': '-', 'Times': '*', 'Divide': '/', 'Mod': '%', 'Power': '**',
                      'Eq': '==', 'NotEq': '!=', 'Eql': '===', 'NotEql': '!==', 'LessThan': '<',
                      'GreaterThan': '>', 'LessEq': '<=', 'GreaterEq': '>=', 'And': '&', 'Xor': '^',
                      'Xnor': '~^', 'Or': '|', 'Land': '&&', 'Lor': '||', 'Ulnot': '!', 'Unot': '~'}

            node_type = expr_node.__class__.__name__
            if node_type == "Cond":
                return {"type": "ternary_op",
                        "condition": self._transform_expression_to_semantic(expr_node.cond),
                        "true_value": self._transform_expression_to_semantic(expr_node.true_value),
                        "false_value": self._transform_expression_to_semantic(expr_node.false_value)}

            if isinstance(expr_node, UnaryOperator):
                return {"type": "unary_op", "operator": op_map.get(node_type, node_type),
                        "operand": self._transform_expression_to_semantic(expr_node.right)}
            else:
                return {"type": "binary_op", "operator": op_map.get(node_type, node_type),
                        "left": self._transform_expression_to_semantic(expr_node.left),
                        "right": self._transform_expression_to_semantic(expr_node.right)}

        elif isinstance(expr_node, Concat):
            return {"type": "concatenation",
                    "parts": [self._transform_expression_to_semantic(p) for p in expr_node.list]}

        elif isinstance(expr_node, Partselect):
            return {"type": "part_select",
                    "variable": self._transform_expression_to_semantic(expr_node.var),
                    "msb": self._transform_expression_to_semantic(expr_node.msb),
                    "lsb": self._transform_expression_to_semantic(expr_node.lsb)}

        elif isinstance(expr_node, Rvalue):
            return self._transform_expression_to_semantic(expr_node.var)

        return {"type": "unknown_expression", "node_type": expr_node.__class__.__name__}

    def _calculate_width(self, width_node) -> int:
        if not width_node: return 1
        msb = self._evaluate_constant_expr(width_node.msb)
        lsb = self._evaluate_constant_expr(width_node.lsb)
        if msb is not None and lsb is not None:
            return abs(msb - lsb) + 1
        return 1

    def _evaluate_constant_expr(self, expr_node) -> Optional[int]:
        if isinstance(expr_node, IntConst):
            try:

                val_str = str(expr_node.value)
                if "'" in val_str:
                    val_str = val_str.split("'")[-1]
                    if val_str.startswith(('h', 'H')): return int(val_str[1:], 16)
                    if val_str.startswith(('d', 'D')): return int(val_str[1:])
                    if val_str.startswith(('b', 'B')): return int(val_str[1:], 2)
                    if val_str.startswith(('o', 'O')): return int(val_str[1:], 8)
                return int(val_str)
            except (ValueError, TypeError):
                return None
        return None

    def _infer_semantic_role(self, name: str) -> str:
        name_lower = name.lower()
        if 'clk' in name_lower or 'clock' in name_lower: return 'clock_signal'
        if 'rst' in name_lower or 'reset' in name_lower: return 'reset_signal'
        if 'led' in name_lower: return 'led_output'
        if 'cnt' in name_lower or 'count' in name_lower: return 'counter'
        if 'en' in name_lower or 'enable' in name_lower: return 'enable_signal'
        if 'data' in name_lower: return 'data_signal'
        if 'addr' in name_lower: return 'address_signal'
        return 'signal'

    def _extract_identifier_name(self, lvalue_node) -> str:
        current_node = lvalue_node

        while hasattr(current_node, 'var'):
            current_node = current_node.var


        while hasattr(current_node, 'var'):
            current_node = current_node.var

        if isinstance(current_node, Identifier):
            return current_node.name
        return "unknown"

    def _infer_functional_summary(self, statement_tree) -> str:
        if not statement_tree: return "unknown"

        tree_str = json.dumps(statement_tree)
        if '"reset_logic"' in tree_str: return "reset_logic"
        if '"counter_logic"' in tree_str: return "counter_logic"
        if '"led_toggle_logic"' in tree_str: return "led_toggle_logic"
        if '"target": "state"' in tree_str or '"target": "state_next"' in tree_str: return "state_machine_logic"

        if statement_tree.get("type") == "conditional":
            cond = statement_tree.get("condition", {})
            if cond.get("type") == "identifier" and "rst" in cond.get("name", "").lower():
                return "reset_logic"

        return "general_logic"



    def _calculate_width(self, width_node) -> int:

        if not width_node:
            return 1

        msb_val = self._evaluate_constant_expr(width_node.msb)
        lsb_val = self._evaluate_constant_expr(width_node.lsb)

        if msb_val is not None and lsb_val is not None:
            return abs(int(msb_val) - int(lsb_val)) + 1

        return 1

    def _evaluate_constant_expr(self, expr_node) -> Optional[int]:
        if isinstance(expr_node, IntConst):
            try:
                return int(expr_node.value)
            except:
                return None
        elif isinstance(expr_node, Identifier):
            if expr_node.name in self.metadata.get('parameters', {}):
                param_val = self.metadata['parameters'][expr_node.name]
                try:
                    return int(param_val)
                except:
                    return None
        return None

    def _infer_semantic_role(self, name: str) -> str:
        name_lower = name.lower()

        if 'clk' in name_lower or 'clock' in name_lower:
            return 'clock_signal'
        elif 'rst' in name_lower or 'reset' in name_lower:
            return 'reset_signal'
        elif 'led' in name_lower:
            return 'led_output'
        elif 'cnt' in name_lower or 'count' in name_lower:
            return 'counter'
        elif 'en' in name_lower or 'enable' in name_lower:
            return 'enable_signal'
        elif 'data' in name_lower:
            return 'data_signal'
        elif 'addr' in name_lower:
            return 'address_signal'

        return 'signal'

    def _extract_identifier_name(self, lvalue_node) -> str:
        if hasattr(lvalue_node, 'var'):
            if isinstance(lvalue_node.var, Identifier):
                return lvalue_node.var.name
            elif isinstance(lvalue_node.var, (Partselect, Pointer)):
                if hasattr(lvalue_node.var, 'var') and isinstance(lvalue_node.var.var, Identifier):
                    return lvalue_node.var.var.name

        return "unknown"

    def _infer_functional_summary(self, statement_tree) -> str:
        if not statement_tree:
            return "unknown"

        if statement_tree.get("type") == "conditional":
            cond = statement_tree.get("condition", {})
            if cond.get("type") == "unary_op" and cond.get("operator") == "logical_not":
                operand = cond.get("operand", {})
                if operand.get("type") == "identifier" and "rst" in operand.get("name", "").lower():
                    return "reset_logic"

            true_branch = statement_tree.get("true_branch", {})
            if true_branch.get("type") == "assignment":
                target = true_branch.get("target", "")
                if "cnt" in target.lower() or "count" in target.lower():
                    return "counter_logic"

        elif statement_tree.get("type") == "assignment":
            target = statement_tree.get("target", "")
            if "led" in target.lower():
                return "led_toggle_logic"

        return "logic"

    @staticmethod
    def parse(input_source: Union[str, Dict, List[str]], debug=False) -> 'RTLParser':
        parser = RTLParser(debug=debug)

        if isinstance(input_source, list):
            parser.metadata['source_type'] = 'RTL_PROJECT_FOLDER'
            parser.metadata['files_parsed'] = input_source
            parser.ast = parser._build_ast_from_files(input_source, source_name=os.path.basename(input_source[0]))

        elif isinstance(input_source, dict):
            return parser.parse_json(input_source)

        elif isinstance(input_source, str):
            if os.path.exists(input_source):
                if os.path.isfile(input_source):
                    return parser.parse_file(input_source)
                elif os.path.isdir(input_source):
                    return parser.parse_folder(input_source)
            else:
                return parser.parse_code(input_source)


        if parser.ast and parser.ast.description:
            module_names = [d.name for d in parser.ast.description.definitions if isinstance(d, ModuleDef)]
            parser.metadata['modules'].extend(m for m in module_names if m not in parser.metadata['modules'])

        return parser

    def _build_ast_from_files(self, filelist: List[str], source_name: str) -> Optional[Source]:

        pyverilog_ast = None


        code_string = ""
        encodings_to_try = ['utf-8', 'gbk', 'latin-1', 'ascii']


        include_paths = list(set([os.path.dirname(f) for f in filelist]))

        for filepath in filelist:
            file_content = None
            for enc in encodings_to_try:
                try:
                    with open(filepath, 'r', encoding=enc, errors='ignore') as f:
                        file_content = f.read()
                    break
                except (UnicodeDecodeError, IOError):
                    continue

            if file_content is not None:
                code_string += file_content + "\n"
            elif self.debug:
                print(f"WARNING: Could not read file '{filepath}' with any tried encoding.")

        if not code_string:
            print(f"ERROR: No valid code could be read from files for project '{source_name}'.")
            return None

        temp_filepath = ''
        try:

            with tempfile.NamedTemporaryFile(mode='w+', suffix='.v', delete=False, encoding='utf-8') as temp_f:
                temp_f.write(code_string)
                temp_filepath = temp_f.name


            ast_tuple = pyverilog_parse([temp_filepath], debug=False)
            pyverilog_ast = ast_tuple[0]
            self.directives = ast_tuple[1]

            if self.debug:
                print(f"Successfully parsed project '{source_name}' with pyverilog.")

        except ParseError as e:
            print(f"SYNTAX ERROR in project '{source_name}' (reported by pyverilog): {str(e)}")
            return None
        except Exception as e:
            print(f"UNEXPECTED PARSING-PHASE ERROR in project '{source_name}': {str(e)}")
            traceback.print_exc()
            return None
        finally:

            if temp_filepath and os.path.exists(temp_filepath):
                os.remove(temp_filepath)

        if pyverilog_ast is None:
            return None

        try:
            converter = VerilogASTConverter()
            converter.debug = self.debug
            custom_ast = converter.convert(pyverilog_ast)
            return custom_ast
        except Exception as e:
            print(f"AST CONVERSION FAILED for project '{source_name}': {str(e)}")
            traceback.print_exc()
            return None


    def _build_ast(self, clean_code: str, source_name: str) -> Source:

        temp_filepath = ''
        try:
            with tempfile.NamedTemporaryFile(mode='w+', suffix='.v', delete=False, encoding='utf-8') as temp_f:
                temp_f.write(clean_code)
                temp_filepath = temp_f.name


            return self._build_ast_from_files([temp_filepath], source_name)

        finally:
            if os.path.exists(temp_filepath):
                os.remove(temp_filepath)


def main():

    if sys.version_info[0] > 2:
        try:
            locale.setlocale(locale.LC_ALL, 'en_US.UTF-8')
            print("[INFO] Locale set to UTF-8.")
        except locale.Error:
            print("[WARN] Could not set locale to UTF-8.")

    if len(sys.argv) > 1:
        input_path_arg = sys.argv[1]
        print("[INFO] Using root project directory from CLI.")
    else:
        input_path_arg = r"......"
        print("[INFO] Using default root project directory.")

    output_dir = r"......"
    print("[INFO] Output directory configured.")

    debug_mode = False

    if not os.path.isdir(input_path_arg):
        print("Error: Root directory not found.")
        sys.exit(1)

    if not os.path.isdir(output_dir):
        print("Error: Output directory not found.")
        sys.exit(1)

    total_files_processed = 0
    successful_files = 0
    failed_files = 0

    for project_name in os.listdir(input_path_arg):
        project_path = os.path.join(input_path_arg, project_name)
        if not os.path.isdir(project_path):
            continue

        verilog_files = []
        for root, _, files in os.walk(project_path):
            for file in files:
                if file.lower().endswith(('.v', '.sv', '.vh')):
                    verilog_files.append(os.path.join(root, file))

        if not verilog_files:
            continue

        for verilog_file_path in verilog_files:
            total_files_processed += 1
            verilog_filename = os.path.basename(verilog_file_path)
            verilog_name_without_ext = os.path.splitext(verilog_filename)[0]
            output_base_name = f"{project_name}_{verilog_name_without_ext}"

            try:
                parser_instance = RTLParser(debug=debug_mode)
                parser_instance.parse_file(verilog_file_path)

                if not parser_instance or not parser_instance.ast or not parser_instance.ast.description.definitions:
                    failed_files += 1
                    continue

                json_filename = f"{output_base_name}_ast.json"
                json_path = os.path.join(output_dir, json_filename)
                with open(json_path, 'w', encoding='utf-8') as f:
                    f.write(parser_instance.get_ast_as_json(indent=2))

                semantic_filename = f"{output_base_name}_semantic.json"
                semantic_path = os.path.join(output_dir, semantic_filename)
                target_module = parser_instance.metadata['modules'][0] if parser_instance.metadata.get('modules') else None
                if target_module:
                    semantic_json = parser_instance.to_semantic_json(target_module_name=target_module)
                    with open(semantic_path, 'w', encoding='utf-8') as f:
                        json.dump(semantic_json if semantic_json else {}, f, ensure_ascii=False, indent=4)

                successful_files += 1

            except Exception:
                if debug_mode:
                    traceback.print_exc()
                failed_files += 1
                continue

    print("=" * 50)
    print("PROCESSING SUMMARY:")
    print(f"Total files processed: {total_files_processed}")
    print(f"Successful: {successful_files}")
    print(f"Failed: {failed_files}")
    print("=" * 50)



if __name__ == "__main__":
    main()

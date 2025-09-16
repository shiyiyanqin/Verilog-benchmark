from __future__ import absolute_import
from __future__ import print_function
import sys
import re
from networkx import DiGraph
import copy as cp


class Node(object):

    def children(self):
        pass

    # traverse the ast tree and initialize the networks.DiGraph
    def toplogic_tree_traverse(self, network_G: DiGraph, rvalue: bool=False, lvalue: bool=False, offset=0):

        indent = 2
        lead = ' ' * offset
        # record the rvalue and lvalue nodes, create current nodes
        rnodes = []  # r value nodes
        lnodes = []  # l value nodes
        cnodes = []  # child nodes for "Always" or "Assign" blocks
        attrlist = {}
        if self.attr_names:
            attrlist = {n: getattr(self, n) for n in self.attr_names}
        # if self.__class__.__name__ in ["AlwaysComb", "Always", "Assign", "ModuleDef", "Identifier", "IntConst", "Rvalue", "Lvalue"]:
            # print(lead, "current nodes = ", attrlist)

        # Record the verilog logic lines
        if hasattr(self, 'end_lineno'):
            lines = (self.lineno, self.end_lineno)
        else:
            lines = (self.lineno, self.lineno)

        # === traverse in the middle ===
        # c_rvalue = False
        # c_lvalue = False
        if self.__class__.__name__ == "Rvalue":
            rvalue = True  # indicate control logic
        if self.__class__.__name__ == "Lvalue":
            lvalue = True  # indicate assignment
        # start traversing
        for c in self.children():
            child_nodes, child_rnodes, child_lnodes  = c.toplogic_tree_traverse(network_G, rvalue, lvalue,
                                                                                offset + indent)
            # get all the child nodes
            cnodes.extend(child_nodes)
            # not possible for hardware language
            rnodes.extend(child_rnodes)
            lnodes.extend(child_lnodes)

        # current nodes
        # Signal node; Special blocks (Always and Assign)


        if self.__class__.__name__ in ["AlwaysComb", "Always", "Assign", "ModuleDef"]:
            if self.__class__.__name__ == "ModuleDef":
                control_name = self.__class__.__name__ + "_" + attrlist['name']
            else: # Target nodes: Try to create the node
                control_name = self.__class__.__name__ + "_lines_" + str(lines[0]) + "_" + str(lines[1])
            if control_name not in network_G:
                network_G.add_node(control_name, line=lines)
            # create edges
            for c in cnodes:
                # lines is the control line for reference
                network_G.add_edge(control_name, c, lines=lines, type=self.__class__.__name__)
            cnodes = []

            cnodes.append(control_name)
        elif 'name' in attrlist and attrlist['name'] != '':
            # print(lead, attrlist, 'adding to rnodes and lnodes', attrlist['name'], rvalue, lvalue)
            # Target nodes: Try to create the node
            if attrlist['name'] not in network_G:
                network_G.add_node(attrlist['name'], lines=lines, type=self.__class__.__name__)
            cnodes.append(attrlist['name'])
            if rvalue:
                rnodes.append(attrlist['name'])
            elif lvalue:
                lnodes.append(attrlist['name'])
        elif self.__class__.__name__ == "IntConst" and rvalue:
            # Target nodes: Try to create the node
            # print(lead, attrlist, "intConst create ", attrlist['value'])
            int_const_name = self.__class__.__name__ + "_" + attrlist['value']
            if attrlist['value'] not in network_G:
                network_G.add_node(int_const_name, lines=lines)
            rnodes.append(int_const_name)

        # === Create Edges ===
        # deal with r value and l value
        # print(lead, attrlist, rnodes, lnodes, rvalue, lvalue)
        if ((not rvalue) and (not lvalue)) and (len(rnodes) > 0 and len(lnodes) > 0):
            # print(lead, attrlist, ' crating edges: ', rnodes, lnodes)
            # r values control l values
            for c in rnodes:
                # lines is the control line for reference
                if c == lnodes[-1]:
                    # avoid self loop
                    continue
                network_G.add_edge(c, lnodes[-1], lines=lines, type=self.__class__.__name__)
            rnodes = []
            lnodes = []
        if self.__class__.__name__ in ["CaseStatement", "IfStatement"]:
            # first child node is the control since we append child node to the back
            for k in range (1, len(cnodes)):
                # use case statement's lines
                network_G.add_edge(cnodes[0], cnodes[k], lines=lines, type=self.__class__.__name__)
        # deal with the parameter
        """
        if self.__class__.__name__ in ["Parameter", "Localparam"] and len(rnodes) > 0:
            assert(len(lnodes) == 0)
            for c in rnodes:
                # lines is the control line for reference
                network_G.add_edge(c, network_G[attrlist['name']], lines=lines, type=self.__class__.__name__)
            rnodes = []
            cnodes = []
        """

        # regular return the value
        # print(lead, "returning ", cnodes, rnodes, lnodes)
        return cnodes, cp.deepcopy(rnodes), cp.deepcopy(lnodes)

    def show(self, buf=sys.stdout, offset=0, attrnames=False, showlineno=True):
        # print("ast show function!")
        indent = 2
        lead = ' ' * offset

        buf.write(lead + self.__class__.__name__ + ': ')

        if self.attr_names:
            if attrnames:
                nvlist = [(n, getattr(self, n)) for n in self.attr_names]
                attrstr = ', '.join('%s=%s' % (n, v) for (n, v) in nvlist)
            else:
                vlist = [getattr(self, n) for n in self.attr_names]
                attrstr = ', '.join('%s' % v for v in vlist)
            buf.write(attrstr)

        if showlineno:
            if hasattr(self, 'end_lineno'):
                buf.write(' (from %s to %s)' % (self.lineno, self.end_lineno))
            else:
                buf.write(' (at %s)' % self.lineno)

        buf.write('\n')

        for c in self.children():
            c.show(buf, offset + indent, attrnames, showlineno)

    def __eq__(self, other):
        if type(self) != type(other):
            return False

        self_attrs = tuple([getattr(self, a) for a in self.attr_names])
        other_attrs = tuple([getattr(other, a) for a in other.attr_names])

        if self_attrs != other_attrs:
            return False

        other_children = other.children()

        for i, c in enumerate(self.children()):
            if c != other_children[i]:
                return False

        return True

    def __ne__(self, other):
        return not self.__eq__(other)

    def __hash__(self):
        s = hash(tuple([getattr(self, a) for a in self.attr_names]))
        c = hash(self.children())
        return hash((s, c))


# ------------------------------------------------------------------------------
class Source(Node):
    attr_names = ('name',)

    def __init__(self, name, description, lineno=0):
        self.lineno = lineno
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
        self.lineno = lineno
        self.definitions = definitions

    def children(self):
        nodelist = []
        if self.definitions:
            nodelist.extend(self.definitions)
        return tuple(nodelist)


class ModuleDef(Node):
    attr_names = ('name',)

    def __init__(self, name, paramlist, portlist, items, default_nettype='wire', lineno=0):
        self.lineno = lineno
        self.name = name
        self.paramlist = paramlist
        self.portlist = portlist
        self.items = items
        self.default_nettype = default_nettype

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
        self.lineno = lineno
        self.params = params

    def children(self):
        nodelist = []
        if self.params:
            nodelist.extend(self.params)
        return tuple(nodelist)


class Portlist(Node):
    attr_names = ()

    def __init__(self, ports, lineno=0):
        self.lineno = lineno
        self.ports = ports

    def children(self):
        nodelist = []
        if self.ports:
            nodelist.extend(self.ports)
        return tuple(nodelist)


class Port(Node):
    attr_names = ('name', 'type',)

    def __init__(self, name, width, dimensions, type, lineno=0):
        self.lineno = lineno
        self.name = name
        self.width = width
        self.dimensions = dimensions
        self.type = type

    def children(self):
        nodelist = []
        if self.width:
            nodelist.append(self.width)
        return tuple(nodelist)


class Width(Node):
    attr_names = ()

    def __init__(self, msb, lsb, lineno=0):
        self.lineno = lineno
        self.msb = msb
        self.lsb = lsb

    def children(self):
        nodelist = []
        if self.msb:
            nodelist.append(self.msb)
        if self.lsb:
            nodelist.append(self.lsb)
        return tuple(nodelist)


class Length(Width):
    pass


class Dimensions(Node):
    attr_names = ()

    def __init__(self, lengths, lineno=0):
        self.lineno = lineno
        self.lengths = lengths

    def children(self):
        nodelist = []
        if self.lengths:
            nodelist.extend(self.lengths)
        return tuple(nodelist)


class Identifier(Node):
    attr_names = ('name',)

    def __init__(self, name, scope=None, lineno=0):
        self.lineno = lineno
        self.name = name
        self.scope = scope

    def children(self):
        nodelist = []
        if self.scope:
            nodelist.append(self.scope)
        return tuple(nodelist)

    def __repr__(self):
        if self.scope is None:
            return self.name
        return self.scope.__repr__() + '.' + self.name


class Value(Node):
    attr_names = ()

    def __init__(self, value, lineno=0):
        self.lineno = lineno
        self.value = value

    def children(self):
        nodelist = []
        if self.value:
            nodelist.append(self.value)
        return tuple(nodelist)


class Constant(Value):
    attr_names = ('value',)

    def __init__(self, value, lineno=0):
        self.lineno = lineno
        self.value = value

    def children(self):
        nodelist = []
        return tuple(nodelist)

    def __repr__(self):
        return str(self.value)


class IntConst(Constant):
    pass


class FloatConst(Constant):
    pass


class StringConst(Constant):
    pass


class Variable(Value):
    attr_names = ('name', 'signed')

    def __init__(self, name, width=None, signed=False, dimensions=None, value=None, lineno=0):
        self.lineno = lineno
        self.name = name
        self.width = width
        self.signed = signed
        self.dimensions = dimensions
        self.value = value

    def children(self):
        nodelist = []
        if self.width:
            nodelist.append(self.width)
        if self.dimensions:
            nodelist.append(self.dimensions)
        if self.value:
            nodelist.append(self.value)
        return tuple(nodelist)


class Input(Variable):
    pass


class Output(Variable):
    pass


class Inout(Variable):
    pass


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
        self.lineno = lineno
        self.first = first
        self.second = second

    def children(self):
        nodelist = []
        if self.first:
            nodelist.append(self.first)
        if self.second:
            nodelist.append(self.second)
        return tuple(nodelist)


class Parameter(Node):
    attr_names = ('name', 'signed')

    def __init__(self, name, value, width=None, signed=False, lineno=0):
        self.lineno = lineno
        self.name = name
        self.value = value
        self.width = width
        self.signed = signed
        self.dimensions = None

    def children(self):
        nodelist = []
        if self.value:
            nodelist.append(self.value)
        if self.width:
            nodelist.append(self.width)
        return tuple(nodelist)


class Localparam(Parameter):
    pass


class Supply(Parameter):
    pass


class Decl(Node):
    attr_names = ()

    def __init__(self, list, lineno=0):
        self.lineno = lineno
        self.list = list

    def children(self):
        nodelist = []
        if self.list:
            nodelist.extend(self.list)
        return tuple(nodelist)


class Concat(Node):
    attr_names = ()

    def __init__(self, list, lineno=0):
        self.lineno = lineno
        self.list = list

    def children(self):
        nodelist = []
        if self.list:
            nodelist.extend(self.list)
        return tuple(nodelist)


class LConcat(Concat):
    pass


class Repeat(Node):
    attr_names = ()

    def __init__(self, value, times, lineno=0):
        self.lineno = lineno
        self.value = value
        self.times = times

    def children(self):
        nodelist = []
        if self.value:
            nodelist.append(self.value)
        if self.times:
            nodelist.append(self.times)
        return tuple(nodelist)


class Partselect(Node):
    attr_names = ()

    def __init__(self, var, msb, lsb, lineno=0):
        self.lineno = lineno
        self.var = var
        self.msb = msb
        self.lsb = lsb

    def children(self):
        nodelist = []
        if self.var:
            nodelist.append(self.var)
        if self.msb:
            nodelist.append(self.msb)
        if self.lsb:
            nodelist.append(self.lsb)
        return tuple(nodelist)


class Pointer(Node):
    attr_names = ()

    def __init__(self, var, ptr, lineno=0):
        self.lineno = lineno
        self.var = var
        self.ptr = ptr

    def children(self):
        nodelist = []
        if self.var:
            nodelist.append(self.var)
        if self.ptr:
            nodelist.append(self.ptr)
        return tuple(nodelist)


class Lvalue(Node):
    attr_names = ()

    def __init__(self, var, lineno=0):
        self.lineno = lineno
        self.var = var

    def children(self):
        nodelist = []
        if self.var:
            nodelist.append(self.var)
        return tuple(nodelist)


class Rvalue(Node):
    attr_names = ()

    def __init__(self, var, lineno=0):
        self.lineno = lineno
        self.var = var

    def children(self):
        nodelist = []
        if self.var:
            nodelist.append(self.var)
        return tuple(nodelist)


# ------------------------------------------------------------------------------
class Operator(Node):
    attr_names = ()

    def __init__(self, left, right, lineno=0):
        self.lineno = lineno
        self.left = left
        self.right = right

    def children(self):
        nodelist = []
        if self.left:
            nodelist.append(self.left)
        if self.right:
            nodelist.append(self.right)
        return tuple(nodelist)

    def __repr__(self):
        ret = '(' + self.__class__.__name__
        for c in self.children():
            ret += ' ' + c.__repr__()
        ret += ')'
        return ret


class UnaryOperator(Operator):
    attr_names = ()

    def __init__(self, right, lineno=0):
        self.lineno = lineno
        self.right = right

    def children(self):
        nodelist = []
        if self.right:
            nodelist.append(self.right)
        return tuple(nodelist)


# Level 1 (Highest Priority)
class Uplus(UnaryOperator):
    pass


class Uminus(UnaryOperator):
    pass


class Ulnot(UnaryOperator):
    pass


class Unot(UnaryOperator):
    pass


class Uand(UnaryOperator):
    pass


class Unand(UnaryOperator):
    pass


class Uor(UnaryOperator):
    pass


class Unor(UnaryOperator):
    pass


class Uxor(UnaryOperator):
    pass


class Uxnor(UnaryOperator):
    pass


# Level 2
class Power(Operator):
    pass


class Times(Operator):
    pass


class Divide(Operator):
    pass


class Mod(Operator):
    pass


# Level 3
class Plus(Operator):
    pass


class Minus(Operator):
    pass


# Level 4
class Sll(Operator):
    pass


class Srl(Operator):
    pass


class Sla(Operator):
    pass


class Sra(Operator):
    pass


# Level 5
class LessThan(Operator):
    pass


class GreaterThan(Operator):
    pass


class LessEq(Operator):
    pass


class GreaterEq(Operator):
    pass


# Level 6
class Eq(Operator):
    pass


class NotEq(Operator):
    pass


class Eql(Operator):
    pass  # ===


class NotEql(Operator):
    pass  # !==


# Level 7
class And(Operator):
    pass


class Xor(Operator):
    pass


class Xnor(Operator):
    pass


# Level 8
class Or(Operator):
    pass


# Level 9
class Land(Operator):
    pass


# Level 10
class Lor(Operator):
    pass


# Level 11
class Cond(Operator):
    attr_names = ()

    def __init__(self, cond, true_value, false_value, lineno=0):
        self.lineno = lineno
        self.cond = cond
        self.true_value = true_value
        self.false_value = false_value

    def children(self):
        nodelist = []
        if self.cond:
            nodelist.append(self.cond)
        if self.true_value:
            nodelist.append(self.true_value)
        if self.false_value:
            nodelist.append(self.false_value)
        return tuple(nodelist)


class Assign(Node):
    attr_names = ()

    def __init__(self, left, right, ldelay=None, rdelay=None, lineno=0):
        self.lineno = lineno
        self.left = left
        self.right = right
        self.ldelay = ldelay
        self.rdelay = rdelay

    def children(self):
        nodelist = []
        if self.left:
            nodelist.append(self.left)
        if self.right:
            nodelist.append(self.right)
        if self.ldelay:
            nodelist.append(self.ldelay)
        if self.rdelay:
            nodelist.append(self.rdelay)
        return tuple(nodelist)


class Always(Node):
    attr_names = ()

    def __init__(self, sens_list, statement, lineno=0):
        self.lineno = lineno
        self.sens_list = sens_list
        self.statement = statement

    def children(self):
        nodelist = []
        if self.sens_list:
            nodelist.append(self.sens_list)
        if self.statement:
            nodelist.append(self.statement)
        return tuple(nodelist)


class AlwaysFF(Always):
    pass


class AlwaysComb(Always):
    pass


class AlwaysLatch(Always):
    pass


class SensList(Node):
    attr_names = ()

    def __init__(self, list, lineno=0):
        self.lineno = lineno
        self.list = list

    def children(self):
        nodelist = []
        if self.list:
            nodelist.extend(self.list)
        return tuple(nodelist)


class Sens(Node):
    attr_names = ('type',)

    def __init__(self, sig, type='posedge', lineno=0):
        self.lineno = lineno
        self.sig = sig
        self.type = type  # 'posedge', 'negedge', 'level', 'all' (*)

    def children(self):
        nodelist = []
        if self.sig:
            nodelist.append(self.sig)
        return tuple(nodelist)


class Substitution(Node):
    attr_names = ()

    def __init__(self, left, right, ldelay=None, rdelay=None, lineno=0):
        self.lineno = lineno
        self.left = left
        self.right = right
        self.ldelay = ldelay
        self.rdelay = rdelay

    def children(self):
        nodelist = []
        if self.left:
            nodelist.append(self.left)
        if self.right:
            nodelist.append(self.right)
        if self.ldelay:
            nodelist.append(self.ldelay)
        if self.rdelay:
            nodelist.append(self.rdelay)
        return tuple(nodelist)


class BlockingSubstitution(Substitution):
    pass


class NonblockingSubstitution(Substitution):
    pass


class IfStatement(Node):
    attr_names = ()

    def __init__(self, cond, true_statement, false_statement, lineno=0):
        self.lineno = lineno
        self.cond = cond
        self.true_statement = true_statement
        self.false_statement = false_statement

    def children(self):
        nodelist = []
        if self.cond:
            nodelist.append(self.cond)
        if self.true_statement:
            nodelist.append(self.true_statement)
        if self.false_statement:
            nodelist.append(self.false_statement)
        return tuple(nodelist)


class ForStatement(Node):
    attr_names = ()

    def __init__(self, pre, cond, post, statement, lineno=0):
        self.lineno = lineno
        self.pre = pre
        self.cond = cond
        self.post = post
        self.statement = statement

    def children(self):
        nodelist = []
        if self.pre:
            nodelist.append(self.pre)
        if self.cond:
            nodelist.append(self.cond)
        if self.post:
            nodelist.append(self.post)
        if self.statement:
            nodelist.append(self.statement)
        return tuple(nodelist)


class WhileStatement(Node):
    attr_names = ()

    def __init__(self, cond, statement, lineno=0):
        self.lineno = lineno
        self.cond = cond
        self.statement = statement

    def children(self):
        nodelist = []
        if self.cond:
            nodelist.append(self.cond)
        if self.statement:
            nodelist.append(self.statement)
        return tuple(nodelist)


class CaseStatement(Node):
    attr_names = ()

    def __init__(self, comp, caselist, lineno=0):
        self.lineno = lineno
        self.comp = comp
        self.caselist = caselist

    def children(self):
        nodelist = []
        if self.comp:
            nodelist.append(self.comp)
        if self.caselist:
            nodelist.extend(self.caselist)
        return tuple(nodelist)


class CasexStatement(CaseStatement):
    pass


class CasezStatement(CaseStatement):
    pass


class UniqueCaseStatement(CaseStatement):
    pass


class Case(Node):
    attr_names = ()

    def __init__(self, cond, statement, lineno=0):
        self.lineno = lineno
        self.cond = cond
        self.statement = statement

    def children(self):
        nodelist = []
        if self.cond:
            nodelist.extend(self.cond)
        if self.statement:
            nodelist.append(self.statement)
        return tuple(nodelist)


class Block(Node):
    attr_names = ('scope',)

    def __init__(self, statements, scope=None, lineno=0):
        self.lineno = lineno
        self.statements = statements
        self.scope = scope

    def children(self):
        nodelist = []
        if self.statements:
            nodelist.extend(self.statements)
        return tuple(nodelist)


class Initial(Node):
    attr_names = ()

    def __init__(self, statement, lineno=0):
        self.lineno = lineno
        self.statement = statement

    def children(self):
        nodelist = []
        if self.statement:
            nodelist.append(self.statement)
        return tuple(nodelist)


class EventStatement(Node):
    attr_names = ()

    def __init__(self, senslist, lineno=0):
        self.lineno = lineno
        self.senslist = senslist

    def children(self):
        nodelist = []
        if self.senslist:
            nodelist.append(self.senslist)
        return tuple(nodelist)


class WaitStatement(Node):
    attr_names = ()

    def __init__(self, cond, statement, lineno=0):
        self.lineno = lineno
        self.cond = cond
        self.statement = statement

    def children(self):
        nodelist = []
        if self.cond:
            nodelist.append(self.cond)
        if self.statement:
            nodelist.append(self.statement)
        return tuple(nodelist)


class ForeverStatement(Node):
    attr_names = ()

    def __init__(self, statement, lineno=0):
        self.lineno = lineno
        self.statement = statement

    def children(self):
        nodelist = []
        if self.statement:
            nodelist.append(self.statement)
        return tuple(nodelist)


class DelayStatement(Node):
    attr_names = ()

    def __init__(self, delay, lineno=0):
        self.lineno = lineno
        self.delay = delay

    def children(self):
        nodelist = []
        if self.delay:
            nodelist.append(self.delay)
        return tuple(nodelist)


class InstanceList(Node):
    attr_names = ('module',)

    def __init__(self, module, parameterlist, instances, lineno=0):
        self.lineno = lineno
        self.module = module
        self.parameterlist = parameterlist
        self.instances = instances

    def children(self):
        nodelist = []
        if self.parameterlist:
            nodelist.extend(self.parameterlist)
        if self.instances:
            nodelist.extend(self.instances)
        return tuple(nodelist)


class Instance(Node):
    attr_names = ('name', 'module')

    def __init__(self, module, name, portlist, parameterlist, array=None, lineno=0):
        self.lineno = lineno
        self.module = module
        self.name = name
        self.portlist = portlist
        self.parameterlist = parameterlist
        self.array = array

    def children(self):
        nodelist = []
        if self.array:
            nodelist.append(self.array)
        if self.parameterlist:
            nodelist.extend(self.parameterlist)
        if self.portlist:
            nodelist.extend(self.portlist)
        return tuple(nodelist)


class ParamArg(Node):
    attr_names = ('paramname',)

    def __init__(self, paramname, argname, lineno=0):
        self.lineno = lineno
        self.paramname = paramname
        self.argname = argname

    def children(self):
        nodelist = []
        if self.argname:
            nodelist.append(self.argname)
        return tuple(nodelist)


class PortArg(Node):
    attr_names = ('portname',)

    def __init__(self, portname, argname, lineno=0):
        self.lineno = lineno
        self.portname = portname
        self.argname = argname

    def children(self):
        nodelist = []
        if self.argname:
            nodelist.append(self.argname)
        return tuple(nodelist)


class Function(Node):
    attr_names = ('name',)

    def __init__(self, name, retwidth, statement, lineno=0):
        self.lineno = lineno
        self.name = name
        self.retwidth = retwidth
        self.statement = statement

    def children(self):
        nodelist = []
        if self.retwidth:
            nodelist.append(self.retwidth)
        if self.statement:
            nodelist.extend(self.statement)
        return tuple(nodelist)

    def __repr__(self):
        return self.name.__repr__()


class FunctionCall(Node):
    attr_names = ()

    def __init__(self, name, args, lineno=0):
        self.lineno = lineno
        self.name = name
        self.args = args

    def children(self):
        nodelist = []
        if self.name:
            nodelist.append(self.name)
        if self.args:
            nodelist.extend(self.args)
        return tuple(nodelist)

    def __repr__(self):
        return self.name.__repr__()


class Task(Node):
    attr_names = ('name',)

    def __init__(self, name, statement, lineno=0):
        self.lineno = lineno
        self.name = name
        self.statement = statement

    def children(self):
        nodelist = []
        if self.statement:
            nodelist.extend(self.statement)
        return tuple(nodelist)


class TaskCall(Node):
    attr_names = ()

    def __init__(self, name, args, lineno=0):
        self.lineno = lineno
        self.name = name
        self.args = args

    def children(self):
        nodelist = []
        if self.name:
            nodelist.append(self.name)
        if self.args:
            nodelist.extend(self.args)
        return tuple(nodelist)


class GenerateStatement(Node):
    attr_names = ()

    def __init__(self, items, lineno=0):
        self.lineno = lineno
        self.items = items

    def children(self):
        nodelist = []
        if self.items:
            nodelist.extend(self.items)
        return tuple(nodelist)


class SystemCall(Node):
    attr_names = ('syscall',)

    def __init__(self, syscall, args, lineno=0):
        self.lineno = lineno
        self.syscall = syscall
        self.args = args

    def children(self):
        nodelist = []
        if self.args:
            nodelist.extend(self.args)
        return tuple(nodelist)

    def __repr__(self):
        ret = []
        ret.append('(')
        ret.append('$')
        ret.append(self.syscall)
        for a in self.args:
            ret.append(' ')
            ret.append(str(a))
        ret.append(')')
        return ''.join(ret)


class IdentifierScopeLabel(Node):
    attr_names = ('name', 'loop')

    def __init__(self, name, loop=None, lineno=0):
        self.lineno = lineno
        self.name = name
        self.loop = loop

    def children(self):
        nodelist = []
        return tuple(nodelist)


class IdentifierScope(Node):
    attr_names = ()

    def __init__(self, labellist, lineno=0):
        self.lineno = lineno
        self.labellist = labellist

    def children(self):
        nodelist = []
        if self.labellist:
            nodelist.extend(self.labellist)
        return tuple(nodelist)


class Pragma(Node):
    attr_names = ()

    def __init__(self, entry, lineno=0):
        self.lineno = lineno
        self.entry = entry

    def children(self):
        nodelist = []
        if self.entry:
            nodelist.append(self.entry)
        return tuple(nodelist)


class PragmaEntry(Node):
    attr_names = ('name', )

    def __init__(self, name, value=None, lineno=0):
        self.lineno = lineno
        self.name = name
        self.value = value

    def children(self):
        nodelist = []
        if self.value:
            nodelist.append(self.value)
        return tuple(nodelist)


class Disable(Node):
    attr_names = ('dest',)

    def __init__(self, dest, lineno=0):
        self.lineno = lineno
        self.dest = dest

    def children(self):
        nodelist = []
        return tuple(nodelist)


class ParallelBlock(Node):
    attr_names = ('scope',)

    def __init__(self, statements, scope=None, lineno=0):
        self.lineno = lineno
        self.statements = statements
        self.scope = scope

    def children(self):
        nodelist = []
        if self.statements:
            nodelist.extend(self.statements)
        return tuple(nodelist)


class SingleStatement(Node):
    attr_names = ()

    def __init__(self, statement, lineno=0):
        self.lineno = lineno
        self.statement = statement

    def children(self):
        nodelist = []
        if self.statement:
            nodelist.append(self.statement)
        return tuple(nodelist)


class EmbeddedCode(Node):
    attr_names = ('code',)

    def __init__(self, code, lineno=0):
        self.code = code

    def children(self):
        nodelist = []
        return tuple(nodelist)

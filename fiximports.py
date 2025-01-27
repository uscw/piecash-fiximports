#!/usr/bin/env python3

#
# fiximports.py -- Categorize imported transactions according to user-defined
#                  rules.
# 
# Copyright (C) 2025 Ulrich Schwardmann <UlrichSchwardmann [at] web.de>
# Copyright (C) 2013 Sandeep Mukherjee <mukherjee.sandeep@gmail.com>
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License as
# published by the Free Software Foundation; either version 3 of
# the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, contact:
# Free Software Foundation           Voice:  +1-617-542-5942
# 51 Franklin Street, Fifth Floor    Fax:    +1-617-542-2652
# Boston, MA  02110-1301,  USA       gnu@gnu.org
#

# @file
#   @brief Categorize imported transactions according to user-defined rules.
#   @author Ulrich Schwardmann <UlrichSchwardmann [at] web.de>
#   @author Sandeep Mukherjee <mukherjee.sandeep@gmail.com>
#
# When GnuCash imports a OFX/QFX file, it adds all transactions to an
# "Imbalance" account, typically "Imbalance-USD" (unless Bayesian matching
# is enabled)
# This script allows you to modify the target account according to rules
# you create. For example, you can specify that a credit-card transaction
# with a description starting with "PIZZA" be categorized as "Expenses:Dining".
# To do this, you need to create a "rules" file first. See rules.txt for
# more information on the format.
# This script can search in the description or the memo fields.
#
# - Rules can be defined as a list of partial rules separated by '&&'. 
# - In this case the conditions of all partial rules have to be fulfilled.
# - Partial rules furthermore can be inverted by a leading '!!'.
# - If a partial rule only uses upper case characters, the rule ignores cases.
# - Imbalance account name pattern can be given as additional requirement for
#   the account name to be fixed.
# - Offset account allows a modification of the fix account only, if this 
#   offset account is involved in the transaction.
 
VERSION = "0.4Beta"

# python imports
import argparse
import logging
from datetime import datetime
from datetime import date
import re
import sys,traceback
import os

# piecash imports
from piecash import open_book, ledger, Split

now_iso = datetime.now().isoformat()

class Rules():
    def __init__(self,filename):
        self.rules = self.readrules(filename)
        
    def readrules(self,filename):
        '''Read the rules file.
        Populate a list with results. The list contents are:
        ([pattern], [account name]), ([pattern], [account name]) ...
        Note: rules list is in the order from the file.
        '''
        rules = []
        with open(filename, 'r') as fd:
            for line in fd:
                line = line.strip()
                if line and not line.startswith('#'):
                    compiled = {}
                    if line.startswith('"'):
                        logging.debug('Using "-escpaped account in rule')
                        result = re.match(r"^\"([^\"]+)\"\s+(.+)", line)
                    else:                       	       
                        result = re.match(r"^(\S+)\s+(.+)", line)
                    if result:
                        ac = result.group(1)
                        pattern = result.group(2)
                        for subpattern in pattern.split("&&"):
                            if subpattern[0:2] == "!!":
                                subpattern = subpattern[2:].strip()
                                if subpattern.upper() == subpattern:
                                    compiled[re.compile(subpattern, re.IGNORECASE)] = False
                                else:
                                    compiled[re.compile(subpattern)] = False
                            else:
                                subpattern = subpattern.strip()
                                if subpattern.upper() == subpattern:
                                    compiled[re.compile(subpattern, re.IGNORECASE)] = True
                                else:
                                    compiled[re.compile(subpattern)] = True
                        rules.append((compiled, ac))
                        logging.debug('Found rule %s for account %s' % ( pattern, ac ) )
                    else:
                        logging.warn('Ignoring line: (incorrect format): "%s"', line)
        rules.reverse()
        return rules

### end class Rules() ***
    
class Accounts():
    def __init__(self, book, args):
        self.args = args
        self.book = book
        self.root_account = book.root_account
        return

    def account_from_path(self, account_path, account=None, original_path=None):
        if original_path is None:
            original_path = account_path
        if account is None:
            account = self.root_account
        account_name, account_path = account_path[0], account_path[1:]
        account = self.get_account_from_Children(account, account_name)
        if account is None:
            raise Exception(
                "A/C path " + ''.join(original_path) + " could not be found")
        else:
            None # logging.debug(' Account found for %s',  account.fullname )
        if len(account_path) > 0:
            return self.account_from_path(account_path, account, original_path)
        else:
            self.account = account
            return account
        
    def get_ac_from_str(self, search_str, rules):
        for patternlist, acpath in rules:
            patternstr = ""
            matchCount = 0
            for pattern in patternlist:
                # patternstr += str(pattern) + " "
                if pattern.search(search_str):
                    if patternlist[pattern] == True: # pattern found and should be found
                        logging.debug('"%s" matches pattern "%s"', search_str, pattern.pattern)
                        matchCount += 1
                else:
                    if patternlist[pattern] == False: # pattern not found and should not be found
                        logging.debug('"%s" matches NOT pattern to find !! "%s"', search_str, pattern.pattern)
                        matchCount += 1
                    else:
                        None # logging.debug('"%s" does not match pattern "%s"', search_str, pattern.pattern)
            if matchCount == len(patternlist):
                acplist = re.split(':', acpath)
                newac = self.account_from_path(acplist)
                return newac, search_str
        return "", ""

    def get_account_from_Children(self,top_acc,acc_name):
        for acc in top_acc.children:
            if acc.name == acc_name:
                return acc
        return None

    def fix_accs_from_rules(self,fix_acc,rules):
        self.total = 0
        self.options = 0
        self.fixed = 0
        offset_account_pattern = re.compile(self.args.offset_ac)
        fix_acc_splits_copy = fix_acc.splits.copy() # fix_acc.splits is changed by account operation  
        for split in fix_acc_splits_copy:
            self.total += 1
            logging.debug("  rule check for transaction from '{}' to '{}' of '{}' with value '{}'".format(split.transaction.splits[0].account.fullname, split.transaction.splits[1].account.fullname, split.transaction.description, split.transaction.splits[0].value))
            search_str = split.transaction.description
            if self.args.use_memo:
                search_str = split.memo
            target_acc, search_str = self.get_ac_from_str(search_str, rules)
            if target_acc != "":
                logging.info("\tChanging account from: '%s' to '%s' for transaction '%s' at %s", fix_acc.fullname, target_acc.fullname, search_str, split.transaction.post_date)
                if split.transaction.splits[0].account == fix_acc:
                    self.options += 1
                    if offset_account_pattern.match(split.transaction.splits[1].account.name):
                        split.transaction.splits[0].account = target_acc
                        self.fixed += 1
                elif split.transaction.splits[1].account == fix_acc:
                    self.options += 1
                    if offset_account_pattern.match(split.transaction.splits[0].account.name):
                        split.transaction.splits[1].account = target_acc
                        self.fixed += 1

    def is_imbalance_account(self):
        imbalance_pattern = re.compile(self.args.imbalance_ac)
        return imbalance_pattern.match(acname)

### end class Accounts() ***

def make_backup(gnucash_file):
    bck_file = gnucash_file + "." + datetime.now().isoformat().replace("-","").replace(":","").replace(".","").replace("T","")[:] + "fix_bck.gnucash"
    os.system('cp ' + gnucash_file + " " + bck_file)

# Parses command-line arguments.
# Returns an array with all user-supplied values.


def parse_cmdline():
    parser = argparse.ArgumentParser()
    parser.add_argument('-i', '--imbalance_ac',default="(.)*", # xmpl: default="Imbalance-[A-Z]{3}",
                        help="Imbalance account name pattern. Default=Imbalance-[A-Z]{3}")
    parser.add_argument('-o', '--offset_ac', default="(.)*",
                        help="Modify account only, if this offset account is involved")
    parser.add_argument('-V','--version', action='store_true',
                        help="Display version and exit.")
    parser.add_argument('-m', '--use_memo', action='store_true',
                        help="Use memo field instead of description field to match rules.")
    parser.add_argument('-v', '--verbose', action='store_true',
                        help="Verbose (debug) logging.")
    parser.add_argument('-q', '--quiet', action='store_true',
                        help="Suppress normal output (except errors).")
    parser.add_argument('-c', '--change', action='store_true',
                        help="Change gnucash file only with this option.")
    parser.add_argument(
        "ac2fix", help="Full path of account to fix, e.g. Liabilities:CreditCard")
    parser.add_argument("rulesfile", help="Rules file. See doc for format.")
    parser.add_argument("gnucash_file", help="GnuCash file to modify.")
    args = parser.parse_args()

    if args.version:
        print (VERSION)
        exit(0)

    if args.verbose:
        loglevel = logging.DEBUG
    elif args.quiet:
        loglevel = logging.WARN
    else:
        loglevel = logging.INFO
    logging.basicConfig(level=loglevel)
    return args

def main(args):

    try:
        book = open_book(args.gnucash_file, readonly=False)
    except:
        logging.error("ERROR: Session locked? " + str(sys.exc_info()[1]))
        exit(1)

    account_path = re.split(':', args.ac2fix)
    RLS = Rules(args.rulesfile)

    book_acc = Accounts(book, args)
    account_path = args.ac2fix.split(":")
    fix_acc = book_acc.account_from_path(account_path)
    imbalance_pattern = re.compile(args.imbalance_ac)
    if not imbalance_pattern.match(fix_acc.name):
        logging.error("\tAccount to fix: '%s' does not match the imbalance_pattern: '%s'! Try adapting --imbalance_ac parameter accordingly.", fix_acc.name, args.imbalance_ac)
        sys.exit(1)
    logging.info("Account to fix: '%s'. Number of transactions: %s",fix_acc.fullname,str(len(fix_acc.splits)))
    book_acc.fix_accs_from_rules(fix_acc, RLS.rules)
    logging.info(" Total: %s, FixOpts: %s, Fixed: %s", book_acc.total, book_acc.options, book_acc.fixed )

    book_acc.book.flush()     # register the changes (but not save)
    if args.change:
        make_backup(args.gnucash_file)
        book_acc.book.save()  # save the book
    else:
        logging.info(" Fix Changes ignored. Use option -c to save changes to gnucash file")

        
#####################    
if __name__ == "__main__":
    args = parse_cmdline()
    main(args)
    

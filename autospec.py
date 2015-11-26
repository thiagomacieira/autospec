#!/usr/bin/python3
#
# autospec.py - part of autospec
# Copyright (C) 2015 Intel Corporation
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import argparse
import build
import buildpattern
import buildreq
import config
import files
import git
import lang
import license
import docs
import os
import patches
import re
import specdescription
import sys
import tarball
import test
import types
import commitmessage

from tarball import name
from util import _file_write

sys.path.append(os.path.dirname(__file__))


def write_sources(file):
    """Append additonal source files.
    systemd unit files, gcov and additional source tarballs
    are the currently supported additonal source file types."""
    source_count = 1
    for source in sorted(buildpattern.sources["unit"] +
                         buildpattern.sources["archive"] +
                         buildpattern.sources["tmpfile"] +
                         buildpattern.sources["gcov"]):
        buildpattern.source_index[source] = source_count
        file.write("Source{0}  : {1}\n".format(source_count, source))
        source_count += 1


def write_spec(filename):
    file = open(filename, "w", encoding="utf-8")
    file.write_strip = types.MethodType(_file_write, file)
    file.write("#\n")
    file.write("# This file is auto-generated. DO NOT EDIT\n")
    file.write("# Generated by: autospec.py\n")
    file.write("#\n")

    if config.keepstatic == 1:
        file.write("%define keepstatic 1\n")

    # first, write the general package header
    tarball.write_nvr(file)
    write_sources(file)
    specdescription.write_summary(file)
    license.write_license(file)

    files.write_main_subpackage_requires(file)
    buildreq.write_buildreq(file)
    patches.write_patch_header(file)

    # then write the main package extra content
    specdescription.write_description(file)
    files.write_files_header(file)

    # then write the build instructions
    buildpattern.write_buildpattern(file)

    # then write the scriplets
    files.write_scriplets(file)

    # then write the %files
    files.write_files(file)
    lang.write_lang_files(file)

    file.close()


def add_sources(download_path, archives):
    for file in os.listdir(download_path):
        if re.search(".*\.(mount|service|socket|target)$", file):
            buildpattern.sources["unit"].append(file)
    buildpattern.sources["unit"].sort()
    #
    # systemd-tmpfiles uses the configuration files from
    # /usr/lib/tmpfiles.d/ directories to describe the creation,
    # cleaning and removal of volatile and temporary files and
    # directories which usually reside in directories such as
    # /run or /tmp.
    #
    if os.path.exists(os.path.normpath(build.download_path +
                                       "/{0}.tmpfiles".format(tarball.name))):
        buildpattern.sources["tmpfile"].append(
            "{}.tmpfiles".format(tarball.name))
    if tarball.gcov_file:
        buildpattern.sources["gcov"].append(tarball.gcov_file)
    for archive, destination in zip(archives[::2], archives[1::2]):
        buildpattern.sources["archive"].append(archive)
        buildpattern.archive_details[archive + "destination"] = destination


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-g", "--skip-git",
                        action="store_false", dest="git", default=True,
                        help="Don't commit result to git")
    parser.add_argument("-n", "--name", nargs=1,
                        action="store", dest="name", default="",
                        help="Override the package name")
    parser.add_argument("url",
                        help="tarball URL (e.g."
                             " http://example.com/downloads/mytar.tar.gz)")
    parser.add_argument('-a', "--archives", action="store",
                        dest="archives", default=[], nargs='*',
                        help="tarball URLs for additional source archives and"
                        " a location for the sources to be extacted to (e.g."
                        " http://example.com/downloads/dependency.tar.gz"
                        " /directory/relative/to/extract/root )")
    parser.add_argument("-l", "--license-only",
                        action="store_true", dest="license_only",
                        default=False, help="Only scan for license files")
    parser.add_argument("-b", "--skip-bump", dest="bump",
                        action="store_false", default=True,
                        help="Don't bump release number")
    parser.add_argument("-c", "--config", nargs=1, dest="config",
                        action="store", default="common/autospec.conf",
                        help="Set configuration file to use")

    args = parser.parse_args()
    if len(args.archives) % 2 != 0:
        parser.error(argparse.ArgumentTypeError(
                     "-a/--archives requires an even number of arguments"))
    #
    # First, download the tarball, extract it and then do a set
    # of static analysis on the content of the tarball.
    #
    build.setup_patterns()

    tarball.download_tarball(args.url, args.name, args.archives)
    dir = tarball.path
    if args.license_only:
        try:
            with open(os.path.join(build.download_path,
                      tarball.name + ".license"), "r") as dotlic:
                for word in dotlic.read().split():
                    if word.find(":") < 0:
                        license.add_license(word)
        except:
            pass
        license.scan_for_licenses(name, dir)
        exit(0)

    config.config_file = args.config
    config.parse_config_files(build.download_path, args.bump)
    config.parse_existing_spec(build.download_path, tarball.name)

    buildreq.scan_for_configure(name, dir, build.download_path)
    specdescription.scan_for_description(name, dir)
    license.scan_for_licenses(name, dir)
    docs.scan_for_changes(build.download_path, dir)
    add_sources(build.download_path, args.archives)
    test.scan_for_tests(dir)

    #
    # Now, we have enough to write out a specfile, and try to build it.
    # We will then analyze the build result and learn information until the
    # package builds
    #
    write_spec(build.download_path + "/" + tarball.name + ".spec")

    print("\n")
    while 1:
        build.package()
        write_spec(build.download_path + "/" + tarball.name + ".spec")
        files.newfiles_printed = 0
        if build.round > 20 or build.must_restart == 0:
            break

    test.check_regression(build.download_path)

    if build.success == 0:
        print("Build failed")
        return

    with open(build.download_path + "/release", "w") as fp:
        fp.write(tarball.release + "\n")

    commitmessage.guess_commit_message()

    if args.git:
        git.commit_to_git(build.download_path)


if __name__ == '__main__':
    main()

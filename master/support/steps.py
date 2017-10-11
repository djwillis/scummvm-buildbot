from os import path

from buildbot.plugins import steps
from buildbot.status import builder
from buildbot.process.buildstep import BuildStep, BuildStepFailed, ShellMixin
from buildbot.steps.worker import CompositeStepMixin
from twisted.internet import defer

class FileExistsSetProperty(steps.FileExists):
    name = "FileExistsSetProperty"
    renderables = ["property", "file"]

    def __init__(self, property, file, **kwargs):
        self.property = property
        steps.FileExists.__init__(self, file, **kwargs)

    def commandComplete(self, cmd):
        self.setProperty(self.property, not cmd.didFail(), self.name)
        self.finished(builder.SUCCESS)

class Package(BuildStep, ShellMixin, CompositeStepMixin):
    name = "package"
    flunkOnFailure = True
    stopOnFailure = True
    description = "packaging"
    descriptionDone = "packaged"

    renderables = ["package_name", "package_format", "make_target", "strip_binaries"]

    def __init__(self, package_name, package_format="tar.xz", make_target=None, strip_binaries=False, **kwargs):
        kwargs = self.setupShellMixin(kwargs, prohibitArgs=["command"])
        BuildStep.__init__(self, **kwargs)
        self.package_name = package_name
        self.package_format = package_format
        self.make_target = make_target
        self.strip_binaries = strip_binaries

    @defer.inlineCallbacks
    def send_command(self, **kwargs):
        cmd = yield self.makeRemoteShellCommand(**kwargs)
        yield self.runCommand(cmd)
        if cmd.didFail():
            raise BuildStepFailed()
        self.updateSummary()
        defer.returnValue(cmd.stdout.strip())

    @defer.inlineCallbacks
    def run(self):
        if self.strip_binaries or self.make_target is None:
            executable_files = yield self.send_command(command=["make", "print-executables"],
                                                       collectStdout=True)
            assert executable_files

        if self.strip_binaries:
            if self.strip_binaries is True:
                strip = "strip"
            else:
                strip = self.strip_binaries
            yield self.send_command(command=[strip, executable_files.split(" ")])

        # if using a bundle target, then make puts everything we need
        # into a directory with the same name as the bundle_target;
        # otherwise we need to get the files ourselves
        # TODO: Make Makefile always bundle with make bundle, and get
        # rid of this extra machinery just for CI
        if self.make_target is None:
            dist_files = yield self.send_command(command=["make", "print-dists"],
                                                 collectStdout=True)
            bundle_dir = self.package_name
            yield self.runRmdir(path.join(self.workdir, bundle_dir))
            yield self.runMkdir(path.join(self.workdir, bundle_dir))
            yield self.send_command(command=["rsync", "-av", executable_files.split(" "), bundle_dir])
            if dist_files:
                yield self.send_command(command=["cp", "-a", dist_files.split(" "), bundle_dir])
        else:
            assert self.make_target[0] != "/"
            assert "../" not in self.make_target
            bundle_dir = self.make_target + "/"
            yield self.runRmdir(path.join(self.workdir, bundle_dir))
            yield self.send_command(command=["make", self.make_target])

        bundle_dir += "/"
        archive_filename = "%s.%s" % (self.package_name, self.package_format)

        if self.package_format is "zip":
            archiver = ["zip", "-8r", archive_filename, bundle_dir]
        else:
            if self.package_format is "tar.gz":
                compression_flag = "j"
                compression_options = {"GZIP": "-9"}
            elif self.package_format is "tar":
                compression_flag = ""
                compression_options = {}
            else:
                compression_flag = "J"
                compression_options = {"XZ_OPT": "-2"}

            archiver = ["tar",
                        "-cv%sf" % compression_flag,
                        archive_filename,
                        bundle_dir]

        yield self.send_command(command=archiver, env=compression_options)
        yield self.runRmdir(path.join(self.workdir, bundle_dir))
        self.setProperty("package_filename", archive_filename)
        defer.returnValue(builder.SUCCESS)

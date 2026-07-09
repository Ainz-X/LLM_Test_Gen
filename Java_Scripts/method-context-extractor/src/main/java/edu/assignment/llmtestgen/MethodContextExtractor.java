package edu.assignment.llmtestgen;

import com.github.javaparser.ParseProblemException;
import com.github.javaparser.StaticJavaParser;
import com.github.javaparser.ast.CompilationUnit;
import com.github.javaparser.ast.Node;
import com.github.javaparser.ast.body.CallableDeclaration;
import com.github.javaparser.ast.body.ClassOrInterfaceDeclaration;
import com.github.javaparser.ast.body.ConstructorDeclaration;
import com.github.javaparser.ast.body.FieldDeclaration;
import com.github.javaparser.ast.body.InitializerDeclaration;
import com.github.javaparser.ast.body.MethodDeclaration;
import com.opencsv.CSVWriter;
import java.io.IOException;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.nio.file.StandardOpenOption;
import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.Optional;
import java.util.Set;
import java.util.stream.Collectors;
import java.util.stream.Stream;
import picocli.CommandLine;
import picocli.CommandLine.Command;
import picocli.CommandLine.Option;
import picocli.CommandLine.ParameterException;
import sootup.core.inputlocation.AnalysisInputLocation;
import sootup.core.model.SootClass;
import sootup.core.model.SootMethod;
import sootup.core.types.ClassType;
import sootup.core.views.View;
import sootup.java.bytecode.frontend.inputlocation.JavaClassPathAnalysisInputLocation;
import sootup.java.bytecode.frontend.inputlocation.JrtFileSystemAnalysisInputLocation;
import sootup.java.bytecode.frontend.inputlocation.PathBasedAnalysisInputLocation;
import sootup.java.core.views.JavaView;

@Command(
    name = "method-context-extractor",
    mixinStandardHelpOptions = true,
    description = "Extracts method-level bytecode and source context for A3.")
public class MethodContextExtractor implements Runnable {

  private static final String UNAVAILABLE = "unavailable";

  @Option(
      names = "--input",
      description = "Comma-separated directories or jars that contain compiled .class files",
      split = ",",
      required = true)
  private List<String> inputPaths;

  @Option(
      names = "--source-root",
      description = "Repeatable Java source root for resolving class source files",
      split = ",",
      required = true)
  private List<String> sourceRoots;

  @Option(
      names = "--output",
      description = "Destination CSV file",
      required = true)
  private Path outputCsv;

  @Option(
      names = "--include-class",
      description = "Repeatable prefix filter for fully-qualified class names",
      split = ",")
  private List<String> includeClassPrefixes = new ArrayList<>();

  @Option(
      names = "--include-method",
      description = "Repeatable contains-filter for method names",
      split = ",")
  private List<String> includeMethodNames = new ArrayList<>();

  @Option(names = "--append", description = "Append to the output CSV instead of overwriting it")
  private boolean append;

  @Option(names = "--skip-rt", description = "Skip the default JRT analysis location")
  private boolean skipRt;

  @Option(
      names = "--fail-on-empty",
      description = "Fail when no rows are extracted",
      defaultValue = "true")
  private boolean failOnEmpty;

  @Option(names = "--verbose", description = "Print progress information")
  private boolean verbose;

  private final Map<String, SourceClassContext> sourceCache = new HashMap<>();

  public static void main(String[] args) {
    int exitCode = new CommandLine(new MethodContextExtractor()).execute(args);
    System.exit(exitCode);
  }

  @Override
  public void run() {
    try {
      execute();
    } catch (ParameterException e) {
      throw e;
    } catch (Exception e) {
      throw new CommandLine.ExecutionException(new CommandLine(this), e.getMessage(), e);
    }
  }

  private void execute() throws IOException {
    if (inputPaths == null || inputPaths.isEmpty()) {
      throw new ParameterException(new CommandLine(this), "At least one --input path is required.");
    }
    if (sourceRoots == null || sourceRoots.isEmpty()) {
      throw new ParameterException(new CommandLine(this), "At least one --source-root path is required.");
    }

    List<Path> normalizedInputs = normalizePaths(inputPaths);
    List<Path> normalizedSourceRoots = normalizePaths(sourceRoots);

    List<AnalysisInputLocation> locations = new ArrayList<>();
    for (Path path : normalizedInputs) {
      if (!Files.exists(path)) {
        throw new ParameterException(new CommandLine(this), "Input path does not exist: " + path);
      }
      if (Files.isDirectory(path)) {
        locations.add(PathBasedAnalysisInputLocation.create(path, null));
      } else {
        locations.add(new JavaClassPathAnalysisInputLocation(path.toString()));
      }
    }

    if (!skipRt) {
      locations.add(new JrtFileSystemAnalysisInputLocation());
    }

    JavaView view = new JavaView(locations);
    List<String> classFilters = normalizeStrings(includeClassPrefixes);
    List<String> methodFilters = normalizeStrings(includeMethodNames);

    List<MethodContextRecord> extracted = new ArrayList<>();
    try (Stream<? extends SootClass> classStream = view.getClasses()) {
      classStream.forEach(
          sootClass -> processClass(sootClass, normalizedSourceRoots, classFilters, methodFilters, extracted));
    }

    if (extracted.isEmpty() && failOnEmpty) {
      throw new IllegalStateException("No methods matched the provided filters.");
    }

    extracted.sort(Comparator.comparing(MethodContextRecord::fqn));
    writeCsv(extracted);
    System.out.printf(Locale.ROOT, "Extracted %d method(s) into %s%n", extracted.size(), outputCsv);
  }

  private List<Path> normalizePaths(List<String> rawPaths) {
    return rawPaths.stream()
        .filter(Objects::nonNull)
        .map(String::trim)
        .filter(s -> !s.isEmpty())
        .map(Paths::get)
        .map(Path::toAbsolutePath)
        .map(Path::normalize)
        .collect(Collectors.toList());
  }

  private List<String> normalizeStrings(List<String> values) {
    if (values == null) {
      return Collections.emptyList();
    }
    return values.stream()
        .filter(Objects::nonNull)
        .map(String::trim)
        .filter(s -> !s.isEmpty())
        .collect(Collectors.toList());
  }

  private void processClass(
      SootClass sootClass,
      List<Path> normalizedSourceRoots,
      List<String> classFilters,
      List<String> methodFilters,
      List<MethodContextRecord> sink) {

    ClassType classType = sootClass.getType();
    String fqClassName = classType.getFullyQualifiedName();
    if (!classFilters.isEmpty() && classFilters.stream().noneMatch(fqClassName::equals)) {
      return;
    }

    SourceClassContext sourceContext = loadSourceContext(fqClassName, normalizedSourceRoots);
    Set<? extends SootMethod> methods = sootClass.getMethods();
    List<? extends SootMethod> sortedMethods =
        methods.stream()
            .filter(SootMethod::hasBody)
            .sorted(Comparator.comparing(method -> buildFqn(fqClassName, method)))
            .collect(Collectors.toList());

    for (SootMethod method : sortedMethods) {
      if (!methodFilters.isEmpty()
          && methodFilters.stream().noneMatch(filter -> method.getName().contains(filter))) {
        continue;
      }

      String fqn = buildFqn(fqClassName, method);
      String signature = buildSignature(method);

      String jimple;
      try {
        jimple = method.getBody().toString();
      } catch (RuntimeException runtimeException) {
        if (verbose) {
          System.err.printf(
              Locale.ROOT,
              "Skipping %s due to body extraction error: %s%n",
              fqn,
              runtimeException.getMessage());
        }
        continue;
      }

      CallableSource callableSource = sourceContext.resolveCallable(method);
      String methodSource = callableSource != null ? callableSource.source() : UNAVAILABLE;
      String helperSignatures = sourceContext.helperSignaturesFor(callableSource);
      String throwsModifiers =
          callableSource != null ? callableSource.throwsAndModifiers() : sourceContext.fallbackThrowsModifiers();

      sink.add(
          new MethodContextRecord(
              fqn,
              signature,
              jimple,
              methodSource,
              sourceContext.fieldContext(),
              helperSignatures,
              throwsModifiers));

      if (verbose) {
        System.out.printf(Locale.ROOT, "Captured %s%n", fqn);
      }
    }
  }

  private SourceClassContext loadSourceContext(String fqClassName, List<Path> normalizedSourceRoots) {
    return sourceCache.computeIfAbsent(fqClassName, key -> parseSourceContext(key, normalizedSourceRoots));
  }

  private SourceClassContext parseSourceContext(String fqClassName, List<Path> normalizedSourceRoots) {
    String simpleClassName = fqClassName.substring(fqClassName.lastIndexOf('.') + 1);
    Path sourceFile = findSourceFile(fqClassName, normalizedSourceRoots);
    if (sourceFile == null) {
      return new SourceClassContext(UNAVAILABLE, Collections.emptyList(), null, UNAVAILABLE);
    }

    try {
      CompilationUnit compilationUnit = StaticJavaParser.parse(sourceFile);
      Optional<ClassOrInterfaceDeclaration> classDeclOpt =
          compilationUnit.findAll(ClassOrInterfaceDeclaration.class).stream()
              .filter(candidate -> candidate.getNameAsString().equals(simpleClassName))
              .findFirst();
      if (classDeclOpt.isEmpty()) {
        return new SourceClassContext(UNAVAILABLE, Collections.emptyList(), null, UNAVAILABLE);
      }

      ClassOrInterfaceDeclaration classDecl = classDeclOpt.get();
      List<String> fieldBlocks =
          classDecl.getFields().stream()
              .map(this::normalizeSourceBlock)
              .filter(s -> !s.isBlank())
              .collect(Collectors.toList());

      List<CallableSource> callables = new ArrayList<>();
      for (ConstructorDeclaration constructor : classDecl.getConstructors()) {
        callables.add(buildCallableSource(constructor, true));
      }
      for (MethodDeclaration method : classDecl.getMethods()) {
        callables.add(buildCallableSource(method, false));
      }

      Optional<InitializerDeclaration> staticInitializerOpt =
          classDecl.getMembers().stream()
              .filter(member -> member instanceof InitializerDeclaration)
              .map(member -> (InitializerDeclaration) member)
              .filter(InitializerDeclaration::isStatic)
              .findFirst();

      CallableSource staticInitializer = null;
      if (staticInitializerOpt.isPresent()) {
        InitializerDeclaration initializer = staticInitializerOpt.get();
        staticInitializer =
            new CallableSource(
                "<clinit>",
                Collections.emptyList(),
                normalizeSourceBlock(initializer),
                "static initializer",
                false,
                true);
      }

      return new SourceClassContext(
          String.join("\n\n", fieldBlocks).trim(),
          callables,
          staticInitializer,
          sourceFile.toString());
    } catch (IOException | ParseProblemException e) {
      if (verbose) {
        System.err.printf(Locale.ROOT, "Failed to parse %s: %s%n", sourceFile, e.getMessage());
      }
      return new SourceClassContext(UNAVAILABLE, Collections.emptyList(), null, UNAVAILABLE);
    }
  }

  private Path findSourceFile(String fqClassName, List<Path> normalizedSourceRoots) {
    String relative = fqClassName.replace('.', '/') + ".java";
    for (Path sourceRoot : normalizedSourceRoots) {
      Path candidate = sourceRoot.resolve(relative);
      if (Files.exists(candidate)) {
        return candidate;
      }
    }
    return null;
  }

  private CallableSource buildCallableSource(CallableDeclaration<?> declaration, boolean constructor) {
    List<String> parameters =
        declaration.getParameters().stream()
            .map(parameter -> normalizeTypeName(parameter.getTypeAsString()))
            .collect(Collectors.toList());
    String name =
        constructor ? "<init>" : ((MethodDeclaration) declaration).getNameAsString();
    return new CallableSource(
        name,
        parameters,
        normalizeSourceBlock(declaration),
        formatThrowsAndModifiers(declaration),
        constructor,
        false);
  }

  private String normalizeSourceBlock(Node node) {
    return node.toString().replace("\r\n", "\n").trim();
  }

  private String formatThrowsAndModifiers(CallableDeclaration<?> declaration) {
    String modifiers =
        declaration.getModifiers().stream()
            .map(modifier -> modifier.getKeyword().asString())
            .collect(Collectors.joining(" "));
    String thrown =
        declaration.getThrownExceptions().stream()
            .map(referenceType -> referenceType.toString())
            .collect(Collectors.joining(", "));
    if (modifiers.isBlank() && thrown.isBlank()) {
      return "none";
    }
    if (thrown.isBlank()) {
      return modifiers;
    }
    if (modifiers.isBlank()) {
      return "throws " + thrown;
    }
    return modifiers + " | throws " + thrown;
  }

  private String buildFqn(String fqClassName, SootMethod method) {
    String params =
        method.getParameterTypes().stream()
            .map(Object::toString)
            .collect(Collectors.joining(","));
    return fqClassName + "." + method.getName() + "(" + params + ")";
  }

  private String buildSignature(SootMethod method) {
    String params =
        method.getParameterTypes().stream()
            .map(Object::toString)
            .collect(Collectors.joining(","));
    return method.getReturnType() + " " + method.getName() + "(" + params + ")";
  }

  private void writeCsv(List<MethodContextRecord> records) throws IOException {
    boolean fileExists = Files.exists(outputCsv);
    boolean writeHeader = !append || !fileExists;
    Path parent = outputCsv.toAbsolutePath().getParent();
    if (parent != null) {
      Files.createDirectories(parent);
    }

    List<StandardOpenOption> options = new ArrayList<>();
    options.add(StandardOpenOption.CREATE);
    if (append && fileExists) {
      options.add(StandardOpenOption.APPEND);
    } else {
      options.add(StandardOpenOption.TRUNCATE_EXISTING);
    }

    try (Writer writer =
            Files.newBufferedWriter(
                outputCsv, StandardCharsets.UTF_8, options.toArray(new StandardOpenOption[0]));
        CSVWriter csvWriter = new CSVWriter(writer)) {
      if (writeHeader) {
        csvWriter.writeNext(
            new String[] {
              "FQN",
              "Signature",
              "Jimple Code Representation",
              "Method Source",
              "Field Context",
              "Constructor/Helper Signatures",
              "Throws/Modifiers"
            },
            false);
      }
      for (MethodContextRecord record : records) {
        csvWriter.writeNext(
            new String[] {
              record.fqn(),
              record.signature(),
              record.jimple(),
              record.methodSource(),
              record.fieldContext(),
              record.helperSignatures(),
              record.throwsModifiers()
            },
            false);
      }
    }
  }

  private String normalizeTypeName(String rawType) {
    String normalized = rawType.trim();
    normalized = normalized.replace("...", "[]");
    int genericIndex = normalized.indexOf('<');
    if (genericIndex >= 0) {
      normalized = normalized.substring(0, genericIndex);
    }
    normalized = normalized.replace("final ", "").trim();
    int arrayDepth = 0;
    while (normalized.endsWith("[]")) {
      arrayDepth++;
      normalized = normalized.substring(0, normalized.length() - 2).trim();
    }
    if (normalized.contains(".")) {
      normalized = normalized.substring(normalized.lastIndexOf('.') + 1);
    }
    StringBuilder builder = new StringBuilder(normalized);
    for (int i = 0; i < arrayDepth; i++) {
      builder.append("[]");
    }
    return builder.toString();
  }

  private String normalizeSootType(String rawType) {
    String normalized = rawType.trim();
    int arrayDepth = 0;
    while (normalized.endsWith("[]")) {
      arrayDepth++;
      normalized = normalized.substring(0, normalized.length() - 2).trim();
    }
    if (normalized.contains(".")) {
      normalized = normalized.substring(normalized.lastIndexOf('.') + 1);
    }
    StringBuilder builder = new StringBuilder(normalized);
    for (int i = 0; i < arrayDepth; i++) {
      builder.append("[]");
    }
    return builder.toString();
  }

  private record MethodContextRecord(
      String fqn,
      String signature,
      String jimple,
      String methodSource,
      String fieldContext,
      String helperSignatures,
      String throwsModifiers) {}

  private final class SourceClassContext {
    private final String fieldContext;
    private final List<CallableSource> callables;
    private final CallableSource staticInitializer;
    private final String sourcePath;

    private SourceClassContext(
        String fieldContext,
        List<CallableSource> callables,
        CallableSource staticInitializer,
        String sourcePath) {
      this.fieldContext = fieldContext == null || fieldContext.isBlank() ? UNAVAILABLE : fieldContext;
      this.callables = callables;
      this.staticInitializer = staticInitializer;
      this.sourcePath = sourcePath;
    }

    private SourceClassContext empty() {
      return new SourceClassContext(UNAVAILABLE, Collections.emptyList(), null, UNAVAILABLE);
    }

    private String fieldContext() {
      return fieldContext;
    }

    private String fallbackThrowsModifiers() {
      return sourcePath.equals(UNAVAILABLE) ? UNAVAILABLE : "source declaration unavailable";
    }

    private CallableSource resolveCallable(SootMethod sootMethod) {
      if ("<clinit>".equals(sootMethod.getName())) {
        return staticInitializer;
      }

      List<String> sootParams =
          sootMethod.getParameterTypes().stream()
              .map(Object::toString)
              .map(MethodContextExtractor.this::normalizeSootType)
              .collect(Collectors.toList());

      List<CallableSource> nameMatches =
          callables.stream()
              .filter(callable -> callable.matchesName(sootMethod.getName()))
              .collect(Collectors.toList());
      for (CallableSource callable : nameMatches) {
        if (callable.parameterTypes().equals(sootParams)) {
          return callable;
        }
      }
      List<CallableSource> arityMatches =
          nameMatches.stream()
              .filter(callable -> callable.parameterTypes().size() == sootParams.size())
              .collect(Collectors.toList());
      if (arityMatches.size() == 1) {
        return arityMatches.get(0);
      }
      return null;
    }

    private String helperSignaturesFor(CallableSource focal) {
      Map<String, String> ordered = new LinkedHashMap<>();
      for (CallableSource callable : callables) {
        if (focal != null && callable.sameSignature(focal)) {
          continue;
        }
        ordered.put(callable.signatureKey(), callable.humanSignature());
      }
      if (staticInitializer != null && (focal == null || !staticInitializer.sameSignature(focal))) {
        ordered.put(staticInitializer.signatureKey(), staticInitializer.humanSignature());
      }
      if (ordered.isEmpty()) {
        return UNAVAILABLE;
      }
      return String.join("\n", ordered.values());
    }
  }

  private record CallableSource(
      String name,
      List<String> parameterTypes,
      String source,
      String throwsAndModifiers,
      boolean constructor,
      boolean staticInitializer) {

    private boolean matchesName(String sootName) {
      if (constructor && "<init>".equals(sootName)) {
        return true;
      }
      if (staticInitializer && "<clinit>".equals(sootName)) {
        return true;
      }
      return name.equals(sootName);
    }

    private String humanSignature() {
      String params = String.join(", ", parameterTypes);
      if (staticInitializer) {
        return "static initializer";
      }
      if (constructor) {
        return "<init>(" + params + ")";
      }
      return name + "(" + params + ") | " + throwsAndModifiers;
    }

    private String signatureKey() {
      return name + "(" + String.join(",", parameterTypes) + ")";
    }

    private boolean sameSignature(CallableSource other) {
      return signatureKey().equals(other.signatureKey());
    }
  }
}
